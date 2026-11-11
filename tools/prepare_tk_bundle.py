"""Export Tcl/Tk resources, including Python builds using embedded zipfs."""
from pathlib import Path
import tkinter

root = tkinter.Tk()
root.withdraw()


def copy_tree(source, target):
    target.mkdir(parents=True, exist_ok=True)
    entries = root.tk.call('glob', '-nocomplain', '-directory', source, '*')
    for entry in root.tk.splitlist(entries):
        name = root.tk.call('file', 'tail', entry)
        if root.tk.call('file', 'isdirectory', entry):
            copy_tree(entry, target / name)
        else:
            root.tk.call('file', 'copy', '-force', entry, str(target / name))


try:
    destination = Path(__file__).resolve().parents[1] / '.pyinstaller-work' / 'tk-resources'
    for variable, folder in (('tcl_library', '_tcl_data'), ('tk_library', '_tk_data')):
        target = destination / folder
        source = root.tk.getvar(variable)
        copy_tree(source, target)
finally:
    root.destroy()
