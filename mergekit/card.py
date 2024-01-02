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

import os
from collections.abc import Iterable
from typing import Any, List, Optional, Sequence

import huggingface_hub
import yaml
from yaml.nodes import SequenceNode as SequenceNode

from mergekit.config import MergeConfiguration, ModelReference

CARD_TEMPLATE = """---
{metadata}
---
# {name}

This is a merge of pre-trained language models created using [mergekit](https://github.com/cg123/mergekit).

## Merge Details
### Merge Method

This model was merged using the {merge_method} merge method{base_text}.

### Models Merged

The following models were included in the merge:
{model_list}

### Configuration

The following YAML configuration was used to produce this model:

```yaml
{config_yaml}
```
"""


class ConfigYamlDumper(yaml.Dumper):
    """Custom YAML dumper to format lists of numbers in flow style."""

    def represent_list(self, data: Iterable[Any]) -> SequenceNode:
        flow_style = all(isinstance(e, (int, float)) for e in data)
        return self.represent_sequence(
            "tag:yaml.org,2002:seq", data, flow_style=flow_style
        )


ConfigYamlDumper.add_representer(list, ConfigYamlDumper.represent_list)


def is_hf(path: str) -> bool:
    """
    Determines if the given path is a Hugging Face model repository.

    Args:
        path: A string path to check.
    """
    if path[0] in "/~" or path.count("/") > 1:
        return False  # definitely a local path
    if not os.path.exists(path):
        return True  # If path doesn't exist locally, it must be a HF repo
    return huggingface_hub.repo_exists(path, repo_type="model")


def extract_hf_paths(models: List[ModelReference]) -> Sequence[str]:
    """
    Yields all valid Hugging Face paths from a list of ModelReference objects.

    Args:
        models: A list of ModelReference objects.
    """
    for model in models:
        if is_hf(model.path):
            yield model.path

        if model.lora_path and is_hf(model.lora_path):
            yield model.lora_path


def method_md(merge_method: str) -> str:
    """
    Returns a markdown string for the given merge method.

    Args:
        merge_method: A string indicating the merge method used.
    """
    methods = {
        "linear": "[linear](https://arxiv.org/abs/2203.05482)",
        "ties": "[TIES](https://arxiv.org/abs/2306.01708)",
        "slerp": "SLERP",
        "task_arithmetic": "[task arithmetic](https://arxiv.org/abs/2212.04089)",
        "dare_ties": "[DARE](https://arxiv.org/abs/2311.03099) [TIES](https://arxiv.org/abs/2306.01708)",
        "dare_linear": "linear [DARE](https://arxiv.org/abs/2311.03099)",
    }
    return methods.get(merge_method, merge_method)


def maybe_link_hf(path: str) -> str:
    """
    Convert a path to a clickable link if it's a Hugging Face model path.

    Args:
        path: A string path to possibly convert to a link.
    """
    if is_hf(path):
        return f"[{path}](https://huggingface.co/{path})"
    return path


def modelref_md(model: ModelReference) -> str:
    """
    Generates markdown description for a ModelReference object.

    Args:
        model: A ModelReference object.

    Returns:
        A markdown formatted string describing the model reference.
    """
    text = maybe_link_hf(model.path)
    if model.lora_path:
        text += " + " + maybe_link_hf(model.lora_path)
    return text


def generate_card(config: MergeConfiguration, name: Optional[str] = None) -> str:
    """
    Generates a markdown card for a merged model configuration.

    Args:
        config: A MergeConfiguration object.
        name: An optional name for the model.
    """
    if not name:
        name = "Untitled Model (1)"

    actual_base = ModelReference.parse(config.base_model) if config.base_model else None
    if config.merge_method == "slerp":
        actual_base = None

    if actual_base:
        models = set(config.referenced_models()).difference({actual_base})
        base_list = [actual_base] + list(models)
    else:
        base_list = config.referenced_models()

    hf_bases = list(extract_hf_paths(base_list))
    tags = ["mergekit", "merge"]

    model_bullets = []
    for model in base_list:
        if model == actual_base:
            continue

        model_bullets.append("* " + modelref_md(model))

    base_text = ""
    if actual_base:
        base_text = f" using {modelref_md(actual_base)} as a base"
    return CARD_TEMPLATE.format(
        metadata=yaml.dump({"base_model": hf_bases, "tags": tags}),
        model_list="\n".join(model_bullets),
        base_text=base_text,
        merge_method=method_md(config.merge_method),
        name=name,
        config_yaml=yaml.dump(
            config.model_dump(exclude_defaults=True, mode="json"),
            Dumper=ConfigYamlDumper,
        ).rstrip(),
    )
