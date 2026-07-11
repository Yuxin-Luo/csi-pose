# 12 — Cam FPS Probe (4cc × Resolution 矩阵)

**状态**：✅ 完成（2026-07-11 22:50）
**目的**：在本机 USB2 bus 上实测 webcam 真实帧率，决定 cam_capture.py 默认 FOURCC + 分辨率
**决策**：MJPG **640x360** @ 30fps 是**唯一真 30fps** 选项
**工具**：`host/tools/probe_fps.py`（200 帧 timing-based）

---

## 1. 工具

```bash
# 单测一组
python host/tools/probe_fps.py --fourcc MJPG --width 1280 --height 720

# 矩阵全测
for w in 1920 1280 640; do
  for fcc in MJPG YUYV; do
    python host/tools/probe_fps.py --fourcc $fcc --width $w --height 360
  done
done
```

输出 4 行：
```
[probe] req: ...
[probe] got: ...    (cam 协商的格式 + CAP_PROP_FPS)
[probe] measured: ...  (timing 实测 fps)
[probe] verdict: ...   (OK 30fps / OK 15fps / WARN)
```

**裁决原则**：以 **timing 实测 fps** 为准（CAP_PROP_FPS 在 MSMF 后端会"虚高"）。

---

## 2. 测试结果

| # | FOURCC | 分辨率 | got CAP_PROP_FPS | **real fps** | verdict |
|---|---|---|---|---|---|
| 1 | MJPG | 1920x1080 | 30 | **15.28** | OK 15fps |
| 2 | MJPG | 1280x720 | 30 | **15.00** | OK 15fps |
| 3 | **MJPG** | **640x360** | **30** | **28.30** | **OK 30fps ✅** |
| 4 | YUYV | 1920x1080 | 10 | 1.92 | WARN very low |
| 5 | YUYV | 1280x720 | 15 | 5.23 | WARN low |
| 6 | YUYV | 640x360 | 30 | 15.01 | OK 15fps |

**唯一真 30fps：MJPG 640x360**（实测 28.30）。

---

## 3. 决策：MJPG 640x360

### 3.1 为什么不是 720p MJPG？

- cam 报 30fps，但实际只 15fps（USB2 带宽上限）
- mp4 metadata 会写 30fps → ffprobe duration = frames/30 = 944/30 = 31.5s（实际 65s）
- 教师/下游用 h5/video/t_ns 是正确时间线，mp4 metadata 仅人眼可见错觉
- **但**：被人问"mp4 怎么 30 秒就播完？"会很烦

### 3.2 为什么不用 1080p MJPG？

- 也是 15fps，且 mp4 文件更大
- RTMPose 在 1080p 上不一定更准（pose 模型对小图已有足够精度）

### 3.3 YUYV 为什么淘汰？

- 在 1920x1080 / 1280x720 上实际帧率严重低于协商值
- 640x360 也只 15fps
- MJPG 同码率色彩比 YUYV 好（YUYV 是 raw，MJPG 压缩可控）

---

## 4. 应用

| 文件 | 改动 |
|---|---|
| `host/capture/cam_capture.py` | `--width` 默认 1280→**640**, `--height` 默认 720→**360**；help text 说明 USB2 限制 |
| `host/boot_recording.sh` | cam 调用显式 `--width 640 --height 360 --fps 30`（不被 host 端默认值影响）|
| `host/tools/probe_fps.py` | 新增工具，未来换 cam 时复用 |

**commit**：`299cc30 feat(host): cam MJPG 640x360 @30fps (probe-verified) + probe_fps.py tool`

---

## 5. 验收（待用户重跑）

预期 cam_capture 启动打印：
```
[cam] fps: req=30.0 got=30.0 fourcc: req=MJPG got=MJPG
[cam] fps_live ≈ 30（每隔 1s status 报 fps_live=30.0±1）
```

预期 mp4 metadata：
```
ffprobe -v error -show_entries stream=duration,nb_frames,nb_read_packets data/test/s01-smoke-*.mp4
duration ≈ 60s
nb_frames ≈ 1800
```

如果 fps_live < 28 ⇒ 走的不是 640x360，cam 默认没生效。

---

## 6. W1 (mp4 元数据 fps) 自动消失

旧的 W1："mp4 metadata fps=30 但实际 15 ⇒ ffprobe 推算 duration 是实际一半"。

**640x360 真 30fps ⇒ mp4 metadata fps=30 == real fps ⇒ W1 关闭**。无需任何代码改动。

---

## 7. 相关决策追溯

| 决策 | 来源 |
|---|---|
| MJPG 640x360 而不是 720p | probe 结果 (本文件 §2) |
| 信任 timing fps 不信 CAP_PROP_FPS | MSMF 后端虚高是已知问题（cam_capture.py 老注释也提到）|
| 帮助文本明确写"USB2 bandwidth capped" | dev_doc/10 §1 强调的"可追溯"原则 |
| 不再支持 720p / 1080p 默认 | 让未来 agent 不要绕回来；如果换 cam 重跑 probe 即可 |

---

## 8. 不在本次

- ❌ 不支持 USB3 bus 上的 1080p 真 30fps（用户硬件固定）
- ❌ 不改 cam_capture.py 的 FPS 自动探测逻辑（probe 已经是 single source of truth）
- ❌ 不用 YUYV（probe 验证不可用）

---

**维护者**：Claude
**依据**：用户 2026-07-11 22:30 反馈 "15fps 可能不够，需新 cam"，本 probe 给定硬件能力上限
**下次复现时机**：换 cam / 换 USB hub / 换 PC 时重跑 probe
