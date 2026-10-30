import unittest
from decimal import Decimal as D
from extractube import screen_changes as changes

class B05Tests(unittest.TestCase):
    def test_tile_edges_cover_all_pixels(self):
        tiles=changes.b.tile_layout(641,363)
        self.assertEqual(sum(t['pixel_count'] for t in tiles),641*363)
        changes.b.assert_tiles_cover(tiles,641,363)
        mean,values=changes.b.frame_tile_differences(bytes(641*363),bytes([255])*(641*363),641,363,tiles,80,90)
        self.assertEqual(mean,D(1));self.assertTrue(all(v==1 for v in values))
    def test_threshold_is_strict(self):
        samples=[{'request_indices':[i],'roles':['grid']} for i in range(3)]
        layout=changes.b.tile_layout(80,90)
        rows=[{'index':i,'before_sample':i-1,'after_sample':i,'elapsed_video_seconds':D('.5'),'global_mean':value,'t':D(i)/2,'tile_values':[value]} for i,value in [(1,D('.005')),(2,D('.005000000001'))]]
        result=changes.finish_rows(rows,samples,layout)
        self.assertFalse(result[0]['passes_threshold']);self.assertTrue(result[1]['passes_threshold'])
    def test_actual_time_suppression_precedes_grid(self):
        samples=[{'request_indices':[i],'roles':['grid']} for i in range(4)]
        rows=[{'index':i,'before_sample':i-1,'after_sample':i,'t':t,'peak_score':score,'passing_tile_indices':[tile],'passes_threshold':True} for i,t,score,tile in [(1,D('1'),D('.2'),0),(2,D('1.5'),D('.3'),1),(3,D('2'),D('.4'),0)]]
        changes.b._apply_kept_neighbor_tile_support(rows,rows,samples)
        self.assertEqual([r['peak_action'] for r in rows],['suppress','suppress','keep'])
    def test_grid_only_kept_subset_suppresses_disjoint_kept(self):
        samples=[{'request_indices':[i],'roles':['grid']} for i in range(3)]
        for tiles,expected in [([0],'suppress'),([1],'keep'),([0,1],'keep')]:
            rows=[{'index':i,'before_sample':i-1,'after_sample':i,'t':D(i),'peak_score':D('.3') if i==1 else D('.2'),'passing_tile_indices':[0] if i==1 else tiles,'passes_threshold':True} for i in (1,2)]
            changes.b._apply_kept_neighbor_tile_support(rows,rows,samples)
            self.assertEqual(rows[1]['peak_action'],expected)
    def test_neighbor_statistics_fallback_and_median(self):
        self.assertEqual(changes.b.median([D(1),D(2)]),D('1.5'))
        rows=[{'index':i,'t':D(i)*10} for i in range(5)]
        changes.b._neighbor_selection(rows)
        self.assertTrue(all(r['statistics_mode']=='candidate_except_self' for r in rows))
        rows=[{'index':i,'t':D(i)} for i in range(4)];changes.b._neighbor_selection(rows)
        self.assertTrue(all(r['statistics_mode']=='absolute' for r in rows))
