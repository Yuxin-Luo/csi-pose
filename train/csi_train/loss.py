"""Weighted MSE + confidence.

w = 1[presence]·max(C, 0.2) — C is PAM c matrix (diagonal c_r, off-diagonal c_r·c_c).
MSE(w⊙y_hat, w⊙y) implemented as mean(w²·se). Confidence supervises diagonal 18 only with λ·MSE(c_hat,c),
independent of presence (negative = target 0 is key for c_hat learning).
"""
import torch

C_FLOOR = 0.2
MODES = ("pam_full", "diag_balanced", "diag_only")


def pose_loss(pred, Y, presence, *, mode="pam_full", lam=1.0):
    """pred (B,3,J,J) grid or (B,3,J) vector head -> (total, {"coord","conf"})."""
    if mode not in MODES:
        raise ValueError(f"mode {mode!r} not in {MODES}")
    J = Y.shape[-1]
    d = torch.arange(J, device=Y.device)
    C = Y[:, 2]
    c_diag = C[:, d, d]
    pr = presence.to(Y.dtype)
    if pred.dim() == 3:                                  # vector head — diagonal only
        if mode != "diag_only":
            raise ValueError("vector head is diag_only only (Section 8.3-3 note)")
        w = pr.view(-1, 1) * c_diag.clamp(min=C_FLOOR)
        se = (pred[:, :2] - Y[:, :2, d, d]) ** 2          # (B,2,J)
        coord = (w.unsqueeze(1) ** 2 * se).mean()
        conf = (pred[:, 2] - c_diag).pow(2).mean()
    else:
        w = pr.view(-1, 1, 1) * C.clamp(min=C_FLOOR)      # (B,J,J)
        eye = torch.eye(J, device=Y.device, dtype=Y.dtype)
        if mode == "pam_full":
            m = torch.ones_like(eye)
        elif mode == "diag_balanced":
            m = 1.0 + 16.0 * eye                          # Diagonal x17 count balance (306/18)
        else:
            m = eye
        se = (pred[:, :2] - Y[:, :2]) ** 2                # (B,2,J,J)
        coord = (m * w.unsqueeze(1) ** 2 * se).mean()
        conf = (pred[:, 2, d, d] - c_diag).pow(2).mean()
    total = coord + lam * conf
    # parts are detached tensors — scalar conversion (.item()) is at caller's logging call site (no sync per step)
    return total, {"coord": coord.detach(), "conf": conf.detach()}
