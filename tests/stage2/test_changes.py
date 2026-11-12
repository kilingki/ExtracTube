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

    def test_consolidate_segments_merges_bottom_subtitle_only_and_preserves_any_upper_tile(self):
        samples=[{'sample_index':i,'analysis_width':640,'analysis_height':360,'video_seconds':f'{i*2:.9f}'} for i in range(5)]
        # 640x360 -> 8 cols x 4 rows (tiles 0..23 are upper 75%, tiles 24..31 are bottom 25%).
        diffs=[
            {'index':1,'before_sample':0,'after_sample':1,'global_mean':'0.012','passing_tile_indices':[26,27]},
            {'index':2,'before_sample':1,'after_sample':2,'global_mean':'0.008','passing_tile_indices':[23]},
            {'index':3,'before_sample':2,'after_sample':3,'global_mean':'0.042','passing_tile_indices':[24,25,26,27,28]},
            {'index':4,'before_sample':3,'after_sample':4,'global_mean':'0.001','passing_tile_indices':[]},
        ]
        bounds=[
            {'boundary_id':'c0:b0','difference_index':1,'video_seconds':'2.000000000'},
            {'boundary_id':'c0:b1','difference_index':2,'video_seconds':'4.000000000'},
            {'boundary_id':'c0:b2','difference_index':3,'video_seconds':'6.000000000'},
        ]
        segs=[
            {'segment_index':0,'video_range':{'start':'0.000000000','end':'2.000000000'},'sample_indices':[0]},
            {'segment_index':1,'video_range':{'start':'2.000000000','end':'4.000000000'},'sample_indices':[1]},
            {'segment_index':2,'video_range':{'start':'4.000000000','end':'6.000000000'},'sample_indices':[2]},
            {'segment_index':3,'video_range':{'start':'6.000000000','end':'9.000000000'},'sample_indices':[3,4]},
        ]
        vsegs=changes.consolidate_segments('c0',samples,diffs,bounds,segs)
        # b0 (bottom-only [26,27]) merges g0+g1; b1 (upper tile 23) and b2 (major bottom wipe) MUST NOT merge!
        self.assertEqual([vs['source_segment_indices'] for vs in vsegs],[[0,1],[2],[3]])
        self.assertEqual(vsegs[0]['merged_boundary_ids'],['c0:b0'])
        self.assertEqual(vsegs[0]['representative_sample_index'],1)
        self.assertEqual(vsegs[2]['representative_sample_index'],4)
