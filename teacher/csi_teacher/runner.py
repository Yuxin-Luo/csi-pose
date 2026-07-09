"""rtmlib adapter — RTMDet-m(person)->RTMPose-m (design §7). rtmlib dependency isolated in this module.

PoseRunner protocol:
  detect(frame_bgr) -> (N,5) f32 [x1,y1,x2,y2,score] — all above floor, sorted by score descending
  pose(frame_bgr, bbox4) -> (17,3) f32 [x_px, y_px, score]

det_thr filter is the floor in labels.label_frame quotient — only floor(0.05) is baked in, no need to
re-infer when threshold changes.

Measurements (rtmlib 0.0.15):
  - RTMDet ONNX(coco-obj365-person) output: dets (1,N,5)[x1,y1,x2,y2,score] +
    labels (1,N)[cls_int] — NMS included in export. ratio returned by preprocess, inverse transform needed.
  - RTMDet.__call__ discards scores and returns boxes only -> detect() calls
    preprocess+inference directly.
  - session attribute name: BaseTool.session (onnxruntime.InferenceSession).
"""
import numpy as np

# mmpose deployee ONNX — planned URL(projects/rtmo/...rtmdet_m_640-8xb32_coco-person) returns 404,
# actual path is rtmposev1/onnx_sdk coco-obj365-person export (replace with rtmlib README if 404)
DET_ONNX = ("https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/"
            "rtmdet_m_8xb32-100e_coco-obj365-person-235e8209.zip")
POSE_ONNX = ("https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/"
             "rtmpose-m_simcc-body7_pt-body7_420e-256x192-e48f03d0_20230504.zip")
DET_FLOOR = 0.05
PERSON_CLS = 0


class RtmlibRunner:
    det_model = "RTMDet-m"
    pose_model = "RTMPose-m"

    def __init__(self, device="cuda"):
        try:
            # Pre-load CUDA libs so ORT can find them (ORT 1.21+ API).
            # Without this call, libcublasLt.so.12 is not found -> CUDA EP load fails, CPU fallback (WSL2 measured).
            import onnxruntime as ort
            ort.preload_dlls()
        except Exception:
            pass                                 # Old ORT version — rely on system CUDA
        from rtmlib import RTMDet, RTMPose
        self._det = RTMDet(onnx_model=DET_ONNX, model_input_size=(640, 640),
                           backend="onnxruntime", device=device)
        self._pose = RTMPose(onnx_model=POSE_ONNX, model_input_size=(192, 256),
                             to_openpose=False, backend="onnxruntime", device=device)
        if device == "cuda":
            for s in (self._det.session, self._pose.session):
                prov = s.get_providers()
                if "CUDAExecutionProvider" not in prov:
                    # ORT falls back to CPU when EP load fails — let auto-fallback logic catch it
                    raise RuntimeError(f"CUDA EP not active — providers={prov}")

    def detect(self, frame_bgr):
        """RTMDet inference -> (N,5) [x1,y1,x2,y2,score], all above floor, sorted by score descending.

        RTMDet ONNX(coco-obj365-person) has NMS built into export:
          outputs[0] = dets  (1, N, 5) [x1,y1,x2,y2,score] — model input coordinate system
          outputs[1] = labels (1, N)   int64 class id
        Inverse-transform to original pixel coordinates using ratio returned by preprocess.
        """
        img, ratio = self._det.preprocess(frame_bgr)
        outputs = self._det.inference(img)
        dets, labels = outputs[0], outputs[1]   # (1,N,5), (1,N)
        keep = (labels[0].astype(int) == PERSON_CLS) & (dets[0, :, 4] >= DET_FLOOR)
        out = dets[0][keep].astype(np.float32)
        out[:, :4] /= ratio
        return out[np.argsort(-out[:, 4])]

    def pose(self, frame_bgr, bbox):
        """RTMPose inference -> (17,3) [x_px, y_px, score]."""
        kpts, scores = self._pose(frame_bgr, bboxes=[np.asarray(bbox, np.float32)])
        return np.concatenate([kpts[0], scores[0][:, None]], axis=1).astype(np.float32)


def make_runner(device="auto"):
    if device != "auto":
        return RtmlibRunner(device=device)
    try:
        r = RtmlibRunner(device="cuda")
        print("runner: CUDA EP")
        return r
    except Exception as e:
        print(f"Warning: CUDA failed ({type(e).__name__}: {e}) — CPU fallback (slow)")
        return RtmlibRunner(device="cpu")
