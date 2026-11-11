import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from desktop_app import DesktopApp


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.source = Mock()
        self.state = {'status': 'identified', 'session_active': True,
                      'ecu': {'part_number': 'TEST ECU'}, 'monitor': {'active': False},
                      'raw_values': {'group_001_raw': 'aabb01'}}
        self.source.snapshot.side_effect = lambda: self.state
        self.factory = Mock(return_value=self.source)
        self.app = DesktopApp(self.root, self.factory,
                              lambda: [SimpleNamespace(device='COM99', description='Test cable')])

    def tearDown(self):
        if not self.app.closed:
            self.app.close()

    def poll(self):
        self.root.after_cancel(self.app.poll_id)
        self.app.poll()

    def test_connection_is_explicit_and_reads_require_identification(self):
        self.factory.assert_not_called()
        self.assertEqual(str(self.app.read_button['state']), 'disabled')
        self.app.connect()
        self.factory.assert_called_once_with('COM99', baudrate=9600,
                                             trace=self.app.trace, groups=())
        self.source.start.assert_called_once()
        self.assertEqual(str(self.app.read_button['state']), 'disabled')
        self.poll()
        self.assertEqual(str(self.app.read_button['state']), 'normal')
        self.assertEqual(self.app.identity.get(), 'TEST ECU')
        self.assertEqual(self.app.table.item('001')['values'][1], 'AA BB 01')
        self.app.group.set('003')
        self.app.read()
        self.source.set_groups.assert_called_once_with((3,))
        self.app.close()
        self.source.stop.assert_called_once()

    def test_monitor_and_disconnect(self):
        self.app.connect()
        self.app.monitor()
        self.source.start_monitor.assert_called_once_with((1,), min_period_s=1.0)
        self.state['monitor']['active'] = True
        self.poll()
        self.assertEqual(str(self.app.read_button['state']), 'disabled')
        self.app.monitor()
        self.source.stop_monitor.assert_called_once()
        self.app.disconnect()
        self.source.stop.assert_called_once()
        self.assertEqual(self.app.table.item('001')['values'][1], 'Sem leitura')
        self.assertEqual(str(self.app.connect_button['state']), 'normal')

    def test_missing_port_does_not_start_source(self):
        self.app.port.set('')
        self.app.connect()
        self.factory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
