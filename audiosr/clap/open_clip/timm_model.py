""" timm model adapter

Wraps timm (https://github.com/rwightman/pytorch-image-models) models for use as a vision tower in CLIP model.
"""
from collections import OrderedDict

import torch.nn as nn
from timm.layers import to_2tuple, RotAttentionPool2d, AttentionPool2d, Mlp

try:
    import timm
    # from timm.layers import Mlp, to_2tuple
    # from timm.layers.attention_pool2d import RotAttentionPool2d
    # from timm.layers.attention_pool2d import (
    #     AttentionPool2d as AbsAttentionPool2d,
    # )
except ImportError:
    timm = None

from .utils import freeze_batch_norm_2d


class TimmModel(nn.Module):
    """timm model adapter
    # FIXME this adapter is a work in progress, may change in ways that break weight compat
    """

    def __init__(
        self,
        model_name,
        embed_dim,
        image_size=224,
        pool="avg",
        proj="linear",
        drop=0.0,
        pretrained=False,
    ):
        super().__init__()
        if timm is None:
            raise RuntimeError("Please `pip install timm` to use timm models.")

        self.image_size = to_2tuple(image_size)
        self.trunk = timm.create_model(model_name, pretrained=pretrained)
        feat_size = self.trunk.default_cfg.get("pool_size", None)
        feature_ndim = 1 if not feat_size else 2
        if pool in ("abs_attn", "rot_attn"):
            assert feature_ndim == 2
            # if attn pooling used, remove both classifier and default pool
            self.trunk.reset_classifier(0, global_pool="")
        else:
            # reset global pool if pool config set, otherwise leave as network default
            reset_kwargs = dict(global_pool=pool) if pool else {}
            self.trunk.reset_classifier(0, **reset_kwargs)
        prev_chs = self.trunk.num_features

        head_layers = OrderedDict()
        if pool == "abs_attn":
            head_layers["pool"] = AttentionPool2d(
                prev_chs, feat_size=feat_size, out_features=embed_dim
            )
            prev_chs = embed_dim
        elif pool == "rot_attn":
            head_layers["pool"] = RotAttentionPool2d(prev_chs, out_features=embed_dim)
            prev_chs = embed_dim
        else:
            assert proj, "projection layer needed if non-attention pooling is used."

        # NOTE attention pool ends with a projection layer, so proj should usually be set to '' if such pooling is used
        if proj == "linear":
            head_layers["drop"] = nn.Dropout(drop)
            head_layers["proj"] = nn.Linear(prev_chs, embed_dim)
        elif proj == "mlp":
            head_layers["mlp"] = Mlp(prev_chs, 2 * embed_dim, embed_dim, drop=drop)

        self.head = nn.Sequential(head_layers)

    def lock(self, unlocked_groups=0, freeze_bn_stats=False):
        """lock modules
        Args:
            unlocked_groups (int): leave last n layer groups unlocked (default: 0)
        """
        if not unlocked_groups:
            # lock full model
            for param in self.trunk.parameters():
                param.requires_grad = False
            if freeze_bn_stats:
                freeze_batch_norm_2d(self.trunk)
        else:
            # NOTE: partial freeze requires latest timm (master) branch and is subject to change
            try:
                # FIXME import here until API stable and in an official release
                from timm.models.helpers import group_parameters, group_modules
            except ImportError:
                raise RuntimeError(
                    "Please install latest timm `pip install git+https://github.com/rwightman/pytorch-image-models`"
                )
            matcher = self.trunk.group_matcher()
            gparams = group_parameters(self.trunk, matcher)
            max_layer_id = max(gparams.keys())
            max_layer_id = max_layer_id - unlocked_groups
            for group_idx in range(max_layer_id + 1):
                group = gparams[group_idx]
                for param in group:
                    self.trunk.get_parameter(param).requires_grad = False
            if freeze_bn_stats:
                gmodules = group_modules(self.trunk, matcher, reverse=True)
                gmodules = {k for k, v in gmodules.items() if v <= max_layer_id}
                freeze_batch_norm_2d(self.trunk, gmodules)

    def forward(self, x):
        x = self.trunk(x)
        x = self.head(x)
        return x
