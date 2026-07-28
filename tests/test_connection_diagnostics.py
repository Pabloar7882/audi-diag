import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def test_list_all_com_ports_exists_and_returns_list():
    from kw1281_handler import list_all_com_ports, list_serial_ports

    assert callable(list_all_com_ports)
    assert callable(list_serial_ports)

    ports = list_all_com_ports()
    assert isinstance(ports, list)

    if ports:
        assert all("device" in p for p in ports)
