# Copyright (C) 2024 Charles O. Goddard
#
# This software is free software: you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This software is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program. If not, see http://www.gnu.org/licenses/.

import importlib.resources
import string
from abc import ABC, abstractmethod
from typing import ClassVar, Dict, List, Optional, Tuple, TypeAlias, Union

from pydantic import BaseModel, Field
from transformers import PretrainedConfig
from typing_extensions import Literal

import mergekit._data.architectures


class WeightInfo(BaseModel, frozen=True):
    """Information about an individual weight tensor in a model.

    Attributes:
        name (str):
            The name of the tensor representing the weight.
        is_embed (bool):
            Indicates whether the weight is for an embedding.
        is_lm_head (bool):
            Indicates whether the weight is for a language modeling head.
        input_space (Optional[str]):
            The name of the input space associated with the weight, if applicable.
        output_space (Optional[str]):
            The name of the output space associated with the weight, if applicable.
        optional (bool):
            Indicates whether the weight can be omitted from a model.
        aliases (Optional[List[str]]):
            List of alternative names for the weight, if applicable.
    """

    name: str
    is_embed: bool = False
    is_lm_head: bool = False
    input_space: Optional[str] = None
    output_space: Optional[str] = None
    optional: bool = False
    aliases: Optional[List[str]] = None


class ProceduralSpaceInfo(BaseModel, frozen=True):
    """Defines a procedural space computed from one or more other spaces.

    Currently only supports residual connections.

    Attributes:
        name (str): The name of the space defined.
        type (str): The type of procedural space.
        inputs (List[str]): List of names of spaces used to define this space."""

    name: str
    type: Literal["residual"]
    inputs: List[str]


class ArchitectureInfo(ABC):
    @abstractmethod
    def pre_weights(self, config: PretrainedConfig) -> List[WeightInfo]:
        """Return a list of all weights preceding the first layer."""
        ...

    @abstractmethod
    def post_weights(self, config: PretrainedConfig) -> List[WeightInfo]:
        """Return a list of all weights following the final layer."""
        ...

    @abstractmethod
    def layer_weights(
        self, index: int, config: PretrainedConfig
    ) -> Optional[List[WeightInfo]]:
        """Return a list of all weights associated with a given layer."""
        ...

    @abstractmethod
    def sliceable(self) -> bool:
        """
        Return True if the layers of this architecture can be meaningfully sliced.
        """
        ...

    def num_layers_config_key(self) -> str:
        """Key in config that represents number of layers"""
        return "num_hidden_layers"

    def num_layers(self, config: PretrainedConfig) -> int:
        """Return the number of layers in a model."""
        return getattr(config, self.num_layers_config_key())

    def all_weights(self, config: PretrainedConfig) -> List[WeightInfo]:
        """Return all weights associated with a model."""
        num_layers = self.num_layers(config)
        res = list(self.pre_weights(config))
        for layer_idx in range(num_layers):
            res.extend(self.layer_weights(layer_idx, config))
        res.extend(self.post_weights(config))
        return res

    def procedural_spaces(self, config: PretrainedConfig) -> List[ProceduralSpaceInfo]:
        """Return a list of all procedurally defined spaces in a model."""
        return []

    def has_defined_spaces(self) -> bool:
        """
        Return True if this architecture defines space information needed for
        matching-based merge methods.
        """
        return False


class MappingInfo(BaseModel, frozen=True):
    """Information about a mapping between two models.

    Attributes:
        from_model (str):
            The name of the model from which the mapping originates.
        to_model (str):
            The name of the model to which the mapping applies.
    """

    start_architectures: List[str]
    destination_architectures: List[str]
    pre_weights_mapping: Dict[str, List[str]]
    post_weights_mapping: Dict[str, List[str]]


class Mapping(BaseModel, frozen=True):
    pre_weights: List[str]
    post_weights: List[str]


class ConfiguredArchitectureInfo(BaseModel, frozen=True, arbitrary_types_allowed=True):
    info: ArchitectureInfo
    config: PretrainedConfig
    overrides: Optional[
        Dict[str, List[str]]
    ]  # TODO: check if the optional is necessary

    def num_layers(self) -> int:
        return self.info.num_layers(self.config)

    def pre_weights(self) -> List[WeightInfo]:
        if not self.overrides:
            return self.info.pre_weights(self.config)

        return self.overrides["pre_weights"]

    def post_weights(self) -> List[WeightInfo]:
        if not self.overrides:
            return self.info.post_weights(self.config)

        return self.overrides["post_weights"]

    def layer_weights(self, index: int) -> List[WeightInfo]:
        return self.info.layer_weights(index, self.config)

    def procedural_spaces(self) -> List[ProceduralSpaceInfo]:
        return self.info.procedural_spaces(self.config)

    def all_weights(self) -> List[WeightInfo]:
        return self.info.all_weights(self.config)

    def update_overrides(
        self, overrides: Dict[str, List[str]]
    ) -> "ConfiguredArchitectureInfo":
        return ConfiguredArchitectureInfo(
            info=self.info, config=self.config, overrides=overrides
        )


class JSONLayerTemplates(BaseModel, frozen=True):
    weights: List[WeightInfo]
    procedural_spaces: Optional[List[ProceduralSpaceInfo]] = None


class JSONArchitectureDefinition(BaseModel, frozen=True):
    expected_model_type: str = Field(alias="model_type")
    architectures: List[str]
    pre_weights: List[WeightInfo]
    layer_templates: JSONLayerTemplates
    post_weights: List[WeightInfo]
    procedural_spaces: Optional[List[ProceduralSpaceInfo]] = None
    num_layers_config_key: Optional[str] = None


class TemplateWithArithmetic(string.Template):
    idpattern = r"(?a:[_a-z][_a-z0-9]*([+-]1)?)"


class JsonArchitectureInfo(ArchitectureInfo, BaseModel, frozen=True):
    definition: JSONArchitectureDefinition

    def _substitute(
        self,
        item: Union[WeightInfo, ProceduralSpaceInfo],
        config: PretrainedConfig,
        layer_idx: Optional[int] = None,
    ) -> Union[WeightInfo, ProceduralSpaceInfo]:
        num_layers = self.num_layers(config)
        substitutions = {
            "num_layers": num_layers,
            "num_layers+1": num_layers + 1,
            "num_layers-1": num_layers - 1,
        }
        if layer_idx is not None:
            substitutions.update(
                {
                    "layer_index": layer_idx,
                    "layer_index+1": layer_idx + 1,
                    "layer_index-1": layer_idx - 1,
                }
            )

        obj_dict = item.model_dump(mode="json", exclude_unset=True)
        for key in obj_dict:
            if isinstance(obj_dict[key], str) and "{" in obj_dict[key]:
                obj_dict[key] = TemplateWithArithmetic(obj_dict[key]).substitute(
                    substitutions
                )
        return type(item).model_validate(obj_dict)

    def pre_weights(self, config: PretrainedConfig) -> List[WeightInfo]:
        # assume fleshed out names for now in self.overrides
        weights = [
            self._substitute(wi, config=config) for wi in self.definition.pre_weights
        ]

        if self.overrides:
            weights = [wi for wi in weights if wi.name in self.overrides["pre_weights"]]

        return weights

    def layer_weights(
        self, index: int, config: PretrainedConfig
    ) -> Optional[List[WeightInfo]]:
        return [
            self._substitute(wi, config=config, layer_idx=index)
            for wi in self.definition.layer_templates.weights
        ]

    def post_weights(self, config: PretrainedConfig) -> List[WeightInfo]:
        # assume fleshed out names for now in self.overrides
        weights = [
            self._substitute(wi, config=config) for wi in self.definition.post_weights
        ]

        if self.overrides:
            weights = [
                wi for wi in weights if wi.name in self.overrides["post_weights"]
            ]

        return weights

    def sliceable(self) -> bool:
        return True

    def procedural_spaces(self, config: PretrainedConfig) -> List[ProceduralSpaceInfo]:
        res = []
        for s in self.definition.procedural_spaces or []:
            res.append(self._substitute(s, config=config))
        for idx in range(self.num_layers(config)):
            for s in self.definition.layer_templates.procedural_spaces or []:
                res.append(self._substitute(s, config=config, layer_idx=idx))
        return res

    def has_defined_spaces(self) -> bool:
        if (
            self.definition.procedural_spaces
            or self.definition.layer_templates.procedural_spaces
        ):
            return True
        for wi in (
            self.definition.layer_templates.weights
            + self.definition.pre_weights
            + self.definition.post_weights
        ):
            if wi.input_space or wi.output_space:
                return True
        return False

    def num_layers_config_key(self) -> str:
        return self.definition.num_layers_config_key


class MixtralTensorNames(ArchitectureInfo, BaseModel):
    ARCHITECTURE_NAME: ClassVar[str] = "MixtralForCausalLM"
    num_local_experts: int

    @classmethod
    def from_config(cls, config: PretrainedConfig):
        return MixtralTensorNames(num_local_experts=config.num_local_experts)

    def pre_weights(self, config: PretrainedConfig) -> List[WeightInfo]:
        return MISTRAL_INFO.pre_weights(config)

    def post_weights(self, config: PretrainedConfig) -> List[WeightInfo]:
        return MISTRAL_INFO.post_weights(config)

    def num_layers_config_key(self) -> str:
        return MISTRAL_INFO.num_layers_config_key()

    def layer_weights(
        self, index: int, config: PretrainedConfig
    ) -> Optional[List[WeightInfo]]:
        num_experts = self.num_local_experts
        prefix = f"model.layers.{index}"
        tensor_names = []
        for expert_idx in range(num_experts):
            for param in ("w1", "w2", "w3"):
                tensor_names.append(
                    prefix + f".block_sparse_moe.experts.{expert_idx}.{param}.weight"
                )
        tensor_names.append(prefix + ".block_sparse_moe.gate.weight")
        res = []
        for name in tensor_names:
            res.append(WeightInfo(name=name))
        return res

    def sliceable(self) -> bool:
        return True

    def has_defined_spaces(self) -> bool:
        return False


def _load_json_arch(name: str) -> JsonArchitectureInfo:
    text = importlib.resources.read_text(mergekit._data.architectures, name)
    return JsonArchitectureInfo(
        definition=JSONArchitectureDefinition.model_validate_json(text)
    )


def _load_all_architectures() -> (
    Tuple[List[JsonArchitectureInfo], Dict[str, List[JsonArchitectureInfo]]]
):
    architectures: List[JsonArchitectureInfo] = []
    for f in importlib.resources.contents(mergekit._data.architectures):
        if f.lower().endswith(".json"):
            architectures.append(_load_json_arch(f))

    name_to_arch: Dict[str, List[JsonArchitectureInfo]] = {}
    for arch_info in architectures:
        for name in arch_info.definition.architectures:
            name_to_arch[name] = name_to_arch.get(name, [])
            name_to_arch[name].append(arch_info)
    return architectures, name_to_arch


JSON_ARCHITECTURES, NAME_TO_ARCH = _load_all_architectures()
MISTRAL_INFO = _load_json_arch("mistral.json")


def _load_architecture_info(config: PretrainedConfig) -> ArchitectureInfo:
    if len(config.architectures) != 1:
        raise RuntimeError("More than one architecture in config?")

    arch_name = config.architectures[0]

    if arch_name == MixtralTensorNames.ARCHITECTURE_NAME:
        return MixtralTensorNames.from_config(config)

    if arch_name not in NAME_TO_ARCH:
        raise RuntimeError(f"Unsupported architecture {arch_name}")

    candidates = list(NAME_TO_ARCH[arch_name])
    if len(candidates) == 1:
        return candidates[0]

    for c in candidates:
        if c.definition.expected_model_type == config.model_type:
            return c

    raise RuntimeError(
        f"Unsupported model_type {config.model_type} for architecture {arch_name}"
    )


def _load_arch_mappings(name) -> JsonArchitectureInfo:
    text = importlib.resources.read_text(mergekit._data.mappings, name)
    return MappingInfo.model_validate_json(text)


# TODO: should be immutable map
def _load_all_mappings() -> Dict[str, Dict[str, Dict[str, List[str]]]]:
    mappings: Dict[str, MappingInfo] = {}
    for f in importlib.resources.contents(mergekit._data.mappings):
        if f.lower().endswith(".json"):
            mapping = _load_arch_mappings(f)
            for start_architecture in mapping.start_architectures:
                if start_architecture not in mappings:
                    mappings[start_architecture] = {}

                for destination_architecture in mapping.destination_architectures:
                    if destination_architecture not in mappings[start_architecture]:
                        pre_weights = mapping["pre_weights"]["destination"]
                        post_weights = mapping["post_weights"]["destination"]
                        mappings[start_architecture][destination_architecture] = {
                            "pre_weights": pre_weights,
                            "post_weights": post_weights,
                        }

    # TODO: think about reverse mapping
    return mappings


JSON_MAPPINGS = _load_all_mappings()
