"""WiSPPN CSI->PAM regression network -- unofficial modified implementation.

Source: https://github.com/geekfeiw/WiSPPN 's models/wisppn_resnet.py as base.
Paper: Fei Wang, Stanislav Panev, Ziyi Dai, Jinsong Han, Dong Huang,
      "Can WiFi Estimate Person Pose?", arXiv:1904.00277 (2019).
The original repository has no license file, so the original parts' copyright belongs to the original authors,
and this repository uses it with attribution (see the source/license section of README).
Note: The original ResidualBlock/make_layer skeleton itself is from yunjey/pytorch-tutorial (MIT)
standard ResNet implementation.

Modifications: ① stem 280->150 officially used (original has conv1 not called bug) ② decode final 2ch->3ch(x,y,c_hat)
③ interpolate scale_factor=48 -> size=(144,144) explicit (equivalent) ④ vector_head variant.
"""
import torch.nn as nn
import torch.nn.functional as F


def conv3x3(cin, cout, stride=1):
    return nn.Conv2d(cin, cout, 3, stride=stride, padding=1, bias=False)


class ResidualBlock(nn.Module):
    def __init__(self, cin, cout, stride=1, downsample=None):
        super().__init__()
        self.conv1 = conv3x3(cin, cout, stride)
        self.bn1 = nn.BatchNorm2d(cout)
        self.relu = nn.ReLU(inplace=True)
        self.conv2 = conv3x3(cout, cout)
        self.bn2 = nn.BatchNorm2d(cout)
        self.downsample = downsample

    def forward(self, x):
        residual = x
        out = self.relu(self.bn1(self.conv1(x)))
        out = self.bn2(self.conv2(out))
        if self.downsample is not None:
            residual = self.downsample(x)
        return self.relu(out + residual)


class WiSPPN(nn.Module):
    """(B,in_ch,3,3) -> (B,3,18,18) PAM(x,y,c_hat); vector_head=True -> (B,3,18) diagonal direct."""

    def __init__(self, in_ch=280, width=150, layers=(2, 2, 2, 2), n_joints=18,
                 vector_head=False):
        super().__init__()
        self.n_joints = n_joints
        self.vector_head = vector_head
        self.stem = nn.Sequential(conv3x3(in_ch, width),
                                  nn.BatchNorm2d(width), nn.ReLU(inplace=True))
        self.in_channels = width
        self.layer1 = self._make_layer(width, layers[0])
        self.layer2 = self._make_layer(width, layers[1], 2)
        self.layer3 = self._make_layer(width * 2, layers[2], 2)
        self.layer4 = self._make_layer(width * 2, layers[3], 2)
        if vector_head:
            self.head = nn.Sequential(nn.Linear(width * 2, 64), nn.ReLU(inplace=True),
                                      nn.Linear(64, 3 * n_joints))
        else:
            self.decode = nn.Sequential(            # Original decode + final 3ch (bias=False original preserved)
                conv3x3(width * 2, 64), nn.BatchNorm2d(64), nn.ReLU(inplace=True),
                nn.Conv2d(64, 3, 1, bias=False))

    def _make_layer(self, cout, blocks, stride=1):
        downsample = None
        if stride != 1 or self.in_channels != cout:
            downsample = nn.Sequential(conv3x3(self.in_channels, cout, stride),
                                       nn.BatchNorm2d(cout))
        blocks_ = [ResidualBlock(self.in_channels, cout, stride, downsample)]
        self.in_channels = cout
        blocks_ += [ResidualBlock(cout, cout) for _ in range(blocks - 1)]
        return nn.Sequential(*blocks_)

    def forward(self, x):
        size = self.n_joints * 8                     # 144 — after stride 2 x3 = 18x18
        x = F.interpolate(x, size=(size, size), mode="bilinear", align_corners=False)
        x = self.stem(x)
        x = self.layer4(self.layer3(self.layer2(self.layer1(x))))
        if self.vector_head:
            return self.head(x.mean(dim=(2, 3))).view(-1, 3, self.n_joints)
        return self.decode(x)
