# Models used by hardware/detect.py

Both files are in the repo, so `git pull` (with Wi-Fi on) is all the Mac needs. At run time nothing is downloaded; detection works with Wi-Fi off. Neither is an LLM: they are small vision networks run by OpenCV DNN, which is already a requirement.

| File | What | Source | Licence | SHA-256 |
|---|---|---|---|---|
| `yolox_tiny.onnx` (20 MB) | YOLOX-tiny object detector, 80 COCO classes, 416x416 input. About 21 ms per frame on the NV laptop CPU | Megvii-BaseDetection/YOLOX GitHub release `0.1.1rc0`: `https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx` | Apache-2.0, `LICENSE-YOLOX.txt` | `427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7` |
| `face_detection_yunet_2023mar.onnx` (0.2 MB) | YuNet face detector (OpenCV `FaceDetectorYN`): counts visible faces | opencv/opencv_zoo, `models/face_detection_yunet/` | MIT, `LICENSE-YuNet.txt` | `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4` |

Why these: YOLOX-tiny is Apache-2.0 (the Ultralytics YOLO models are AGPL), runs in plain OpenCV DNN on CPU with no new package, and found more people than YOLOX-nano on the reference image (4 vs 2) for 8 ms more per frame. YuNet replaces the Haar face cascade, which OpenCV 5 no longer ships (nor the HOG people detector).

Check: `python3 -m hardware.detect captures/photos/<some photo>.jpg` prints the detections, the time per frame and the sentence Faraday would use if vision failed.
