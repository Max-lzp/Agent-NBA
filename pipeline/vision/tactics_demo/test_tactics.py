import copy
import json
from pathlib import Path
import unittest
from classify_tactics import classify


class TacticsContractTests(unittest.TestCase):
    def setUp(self):
        self.data=json.loads(Path(__file__).with_name('synthetic_horns_tracks.json').read_text())

    def test_horns_is_candidate_not_probability_or_verified_game(self):
        result=classify(self.data)
        self.assertEqual(len(result['candidates']),4)
        self.assertEqual({h['label'] for h in result['candidates']},{'horns'})
        self.assertTrue(all('requires_video_review' in h['status'] for h in result['candidates']))

    def test_pixel_coordinates_rejected_instead_of_using_meter_thresholds(self):
        self.data['coordinate_unit']='pixels'
        with self.assertRaises(ValueError):classify(self.data)

    def test_camera_cut_and_missing_calibration_do_not_produce_candidate(self):
        for i,f in enumerate(self.data['frames']):f['camera_segment']=str(i)
        self.assertFalse(classify(self.data)['candidates'])
        for f in self.data['frames']:f['calibration_valid']=False
        self.assertFalse(classify(self.data)['candidates'])

    def test_far_apart_possession_transfer_is_not_handoff(self):
        for f in self.data['frames'][3:]:f['ball_handler_track_id']='o4'
        self.assertNotIn('handoff',{h['label'] for h in classify(self.data)['candidates']})

    def test_unreliable_track_prevents_complete_formation_claim(self):
        for f in self.data['frames']:f['players'][1]['tracking_confidence']=.2
        self.assertFalse(classify(self.data)['candidates'])


if __name__=='__main__':unittest.main()
