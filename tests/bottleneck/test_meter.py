"""Profiler must preserve completed calls and avoid double-counting nested spans."""
import importlib.util
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('bottleneck_run', Path(__file__).with_name('run.py'))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class MeterTests(unittest.TestCase):
    def test_completed_call_is_returned_before_next_boundary_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock = [0.]
            with patch.object(runner.time, 'monotonic', side_effect=lambda: clock[0]):
                meter = runner.Meter(Path(tmp), 30, 10)
                def send():
                    clock[0] = 11.
                    return {'complete': True}
                module = types.SimpleNamespace(__name__='transport', send=send)
                meter.wrap(module, 'send')
                self.assertEqual(module.send(), {'complete': True})
                with self.assertRaises(runner.Cutoff):
                    module.send()
                self.assertEqual(meter.stats['transport.send']['calls'], 1)
                meter.events.close()

    def test_nested_self_time_excludes_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock = [0.]
            with patch.object(runner.time, 'monotonic', side_effect=lambda: clock[0]):
                meter = runner.Meter(Path(tmp), 100, 100)
                module = types.SimpleNamespace(__name__='fake')
                def child():
                    clock[0] += 3
                def parent():
                    clock[0] += 2
                    module.child()
                    clock[0] += 1
                module.child, module.parent = child, parent
                meter.wrap(module, 'child')
                meter.wrap(module, 'parent')
                module.parent()
                self.assertEqual(meter.stats['fake.parent']['wall_seconds'], 6)
                self.assertEqual(meter.stats['fake.parent']['self_seconds'], 3)
                self.assertEqual(meter.stats['fake.child']['self_seconds'], 3)
                meter.events.close()

    def test_new_phase_does_not_erase_prior_overrun(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock = [0.]
            with patch.object(runner.time, 'monotonic', side_effect=lambda: clock[0]):
                meter = runner.Meter(Path(tmp), 100, 10)
                clock[0] = 11
                with self.assertRaises(runner.Cutoff):
                    meter.change('next')
                self.assertEqual(meter.phase, 'input')
                meter.events.close()

    def test_unbounded_meter_never_cuts_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock = [0.]
            with patch.object(runner.time, 'monotonic', side_effect=lambda: clock[0]):
                meter = runner.Meter(Path(tmp), None, None)
                def work():
                    clock[0] += 50_000.
                    return 'done'
                module = types.SimpleNamespace(__name__='long_task', work=work)
                meter.wrap(module, 'work')
                self.assertEqual(module.work(), 'done')
                meter.change('t4')
                self.assertEqual(module.work(), 'done')
                finished = meter.finish()
                self.assertEqual(finished['last_phase'], 't4')
                self.assertEqual(finished['functions']['long_task.work']['calls'], 2)
                meter.events.close()


if __name__ == '__main__':
    unittest.main()
