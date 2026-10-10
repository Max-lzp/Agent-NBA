from ultralytics import YOLO
import supervision as sv
import sys 
sys.path.append('../')
from utils import read_stub, save_stub

class PlayerTracker:
    """
    A class that handles player detection and tracking using YOLO and ByteTrack.

    This class combines YOLO object detection with ByteTrack tracking to maintain consistent
    player identities across frames while processing detections in batches.
    """
    def __init__(self, model_path):
        """
        Initialize the PlayerTracker with YOLO model and ByteTrack tracker.

        Args:
            model_path (str): Path to the YOLO model weights.
        """
        self.model = YOLO(model_path) 
        self.tracker = sv.ByteTrack()
        self.ref_tracker = sv.ByteTrack()
        self.ref_tracks = []
        self.suppressed_players = 0

    def detect_frames(self, frames):
        """
        Detect players in a sequence of frames using batch processing.

        Args:
            frames (list): List of video frames to process.

        Returns:
            list: YOLO detection results for each frame.
        """
        batch_size=16 
        detections = [] 
        for i in range(0,len(frames),batch_size):
            detections_batch = self.model.predict(frames[i:i+batch_size],conf=0.5)
            detections += detections_batch
        return detections

    def get_object_tracks(self, frames, read_from_stub=False, stub_path=None):
        """
        Get player tracking results for a sequence of frames with optional caching.

        Args:
            frames (list): List of video frames to process.
            read_from_stub (bool): Whether to attempt reading cached results.
            stub_path (str): Path to the cache file.

        Returns:
            list: List of dictionaries containing player tracking information for each frame,
                where each dictionary maps player IDs to their bounding box coordinates.
        """
        tracks = read_stub(read_from_stub,stub_path)
        if tracks is not None:
            if len(tracks) == len(frames):
                self.ref_tracks = [{} for _ in frames]
                return tracks

        detections = self.detect_frames(frames)

        tracks=[]
        self.ref_tracks = []
        self.suppressed_players = 0

        for detection in detections:
            cls_names_inv = {str(v).lower(): k for k, v in detection.names.items()}
            if "player" not in cls_names_inv:
                raise KeyError(f"球员权重里没有 Player 类，实际类名是 {list(detection.names.values())}")

            parsed = sv.Detections.from_ultralytics(detection)
            player_dets, ref_dets = split_player_and_ref(parsed, cls_names_inv)
            player_map = tracks_to_map(self.tracker.update_with_detections(player_dets))
            ref_map = tracks_to_map(self.ref_tracker.update_with_detections(ref_dets), offset=10_000)
            self.suppressed_players += drop_overlapping(player_map, ref_map)
            tracks.append(player_map)
            self.ref_tracks.append(ref_map)
        
        save_stub(stub_path,tracks)
        return tracks


def split_player_and_ref(parsed, names):
    """Player 和 Ref 分开跟踪。权重里有 Ref，不能和球、记分牌一起送进同一个 ByteTrack。"""
    empty = sv.Detections.empty()
    if parsed.class_id is None or len(parsed) == 0:
        return empty, empty
    players = parsed[parsed.class_id == names["player"]]
    ref_id = names.get("ref")
    refs = parsed[parsed.class_id == ref_id] if ref_id is not None else empty
    return players, refs


def tracks_to_map(tracked, offset=0):
    frame = {}
    if tracked is None or tracked.tracker_id is None:
        return frame
    for xyxy, track_id in zip(tracked.xyxy, tracked.tracker_id):
        if track_id is None:
            continue
        frame[int(track_id) + offset] = {"bbox": [float(v) for v in xyxy]}
    return frame


def box_iou(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    if union <= 0:
        return 0.0
    return inter / union


def drop_overlapping(players, refs, threshold=0.45):
    """同一具身体上球员框和裁判框叠在一起时，留下裁判。"""
    if not refs:
        return 0
    drop = [
        track_id
        for track_id, info in players.items()
        if any(box_iou(info["bbox"], ref["bbox"]) >= threshold for ref in refs.values())
    ]
    for track_id in drop:
        del players[track_id]
    return len(drop)
