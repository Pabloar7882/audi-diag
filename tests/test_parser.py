import unittest
from pathlib import Path
from vcds_parser import load_log
class ParserTest(unittest.TestCase):
    def test_known_vcds_demo(self):
        p=Path(__file__).resolve().parents[1] / 'exemplos' / 'LOG_DEMO_003_011_001.CSV'
        log=load_log(p)
        self.assertEqual(len(log.samples),90)
        d=log.mapped(log.samples[0])
        self.assertEqual(d['rpm'],820)
        self.assertEqual(d['boost_requested_mbar'],1000)
        self.assertEqual(d['boost_actual_mbar'],980)
