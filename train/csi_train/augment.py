# GPU tensor augmentation 4 types — for small-sample enhancement.
# Input: z-score space (B,280,3,3) or (B,560,3,3), train batch only (not for eval/predict).
# Drop/erasing fill value = mu0 = (0-mu)/sigma — normalized value of raw 0 (missing slot),
# matching actual packet/link loss distribution. Time-axis warping types excluded as they degenerate in 5-packet window.
# 560 (amp||phase) input requires mu0_phase — per-block fill value, SC drop uses same k for both blocks.
import torch

N_SC, N_PKT = 56, 5


def mu0_from_stats(mu, sigma):
    # Per-link z-score stats (3,3) -> normalized value of raw 0 (3,3) f32 tensor.
    return -(torch.as_tensor(mu, dtype=torch.float32)
             / torch.as_tensor(sigma, dtype=torch.float32))


def augment_batch(x, mu0, *, mu0_phase=None, p=0.5, noise=0.05, scale=0.25,
                  drop_sc=4, drop_link=0.1):
    # Per-sample Bernoulli(p) gate then: 1) Gaussian noise sigma=noise 2) Bias scale U(1+-scale)
    # (equivalent to contrast scaling in z-score space) 3) SC drop drop_sc count (all packets/links — narrowband fading simulation)
    # 4) Link erasing probability drop_link. 560 (amp||phase) input requires mu0_phase — per-block fill value, SC drop uses same k for both blocks.
    B = x.shape[0]
    C = x.shape[1]
    n_pkt_blocks = C // N_SC                               # 280→5, 560→10
    dev = x.device
    if C == N_SC * N_PKT:
        fill_c = mu0.to(dev)[None].expand(C, 3, 3)
    elif C == 2 * N_SC * N_PKT:
        if mu0_phase is None:
            raise ValueError("560ch(amp||phase) augmentation requires mu0_phase -- phase block fill value")
        fill_c = torch.cat([mu0.to(dev)[None].expand(C // 2, 3, 3),
                            mu0_phase.to(dev)[None].expand(C // 2, 3, 3)])
    else:
        raise ValueError(f"augment_batch: supported channels 280|560, got {C}")
    on = x.new_zeros(B, dtype=torch.bool) if p <= 0 else \
        torch.rand(B, device=dev) < p
    gate = on[:, None, None, None]
    x = x.clone()
    fill = fill_c[None].expand_as(x)
    if noise > 0:
        x = torch.where(gate, x + noise * torch.randn_like(x), x)
    if scale > 0:
        s = 1 + scale * (2 * torch.rand(B, 1, 1, 1, device=dev) - 1)
        x = torch.where(gate, x * s, x)
    if drop_sc > 0:
        r = torch.rand(B, N_SC, device=dev)
        sc = r.argsort(dim=1).argsort(dim=1) < drop_sc     # Equal k items (rank < k)
        ch = sc.repeat(1, n_pkt_blocks)                    # c = p*56+k convention (§6.1), same k for both blocks
        x = torch.where(ch[:, :, None, None] & gate, fill, x)
    if drop_link > 0:
        er = (torch.rand(B, device=dev) < drop_link) & on
        idx = torch.randint(0, 9, (B,), device=dev)
        onehot = torch.zeros(B, 9, dtype=torch.bool, device=dev)
        onehot[torch.arange(B, device=dev), idx] = True
        x = torch.where(onehot.view(B, 1, 3, 3) & er[:, None, None, None],
                        fill, x)
    return x
