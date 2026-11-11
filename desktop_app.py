"""Native Windows diagnostic window. No browser or HTTP server is started."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from diagnostic_monitor import DiagnosticTrace
from diagnostic_source import create_serial_diagnostic_source
from kline_transport import discover_ports


class DesktopApp:
    def __init__(self, root, source_factory=create_serial_diagnostic_source, ports_factory=discover_ports):
        self.root = root
        self.source_factory = source_factory
        self.ports_factory = ports_factory
        self.source = None
        self.trace = DiagnosticTrace()
        self.ports = {}
        self.closed = False
        root.title('FIS AFN — Diagnóstico K-Line')
        root.geometry('1060x800')
        root.minsize(860, 720)
        root.configure(bg='#111416')
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('.', font=('Segoe UI', 10), background='#111416', foreground='#e3e7e5')
        style.configure('TFrame', background='#111416')
        style.configure('TLabel', background='#111416', foreground='#e3e7e5')
        style.configure('Muted.TLabel', foreground='#aab5b7')
        style.configure('Title.TLabel', font=('Segoe UI', 30, 'bold'))
        style.configure('TButton', padding=(12, 8), background='#2b3235', bordercolor='#485154')
        style.map('TButton', background=[('active', '#41494b')], foreground=[('disabled', '#8d999b')])
        style.configure('Accent.TButton', background='#ad2330', foreground='white')
        style.map('Accent.TButton', background=[('active', '#c5232f'), ('disabled', '#45252b')])
        style.configure('TCombobox', fieldbackground='#202729', background='#30383b', arrowcolor='#e3e7e5', padding=6)
        style.map('TCombobox', fieldbackground=[('readonly', '#202729'), ('disabled', '#181d1f')], foreground=[('readonly', '#e3e7e5'), ('disabled', '#8d999b')])
        style.configure('Treeview', background='#1b2124', fieldbackground='#1b2124', foreground='#dce3df', rowheight=28, borderwidth=0)
        style.configure('Treeview.Heading', background='#2b3235', foreground='#bcc6c5', padding=7)
        root.option_add('*TCombobox*Listbox.background', '#202729')
        root.option_add('*TCombobox*Listbox.foreground', '#e3e7e5')
        body = ttk.Frame(root, padding=24)
        body.pack(fill='both', expand=True)
        header = ttk.Frame(body)
        header.pack(fill='x', pady=(0, 10))
        ttk.Label(header, text='AFN', style='Title.TLabel').pack(side='left')
        ttk.Label(header, text='.', foreground='#df3442', font=('Segoe UI', 30, 'bold')).pack(side='left')
        brand = ttk.Frame(header, padding=(16, 0))
        brand.pack(side='left')
        ttk.Label(brand, text='TELEMETRY', font=('Segoe UI', 12, 'bold')).pack(anchor='w')
        ttk.Label(brand, text='A4 B5 AVANT  /  1.9 TDI  /  1999', style='Muted.TLabel').pack(anchor='w')
        ttk.Label(header, text='K-Line  ·  Somente leitura', style='Muted.TLabel').pack(side='right')
        connection = ttk.Frame(body)
        connection.pack(fill='x')
        ttk.Label(connection, text='Cabo / porta COM').grid(row=0, column=0, sticky='w')
        ttk.Label(connection, text='Velocidade').grid(row=0, column=1, sticky='w', padx=12)
        self.port = ttk.Combobox(connection, state='readonly', width=42)
        self.port.grid(row=1, column=0, sticky='ew', pady=6)
        self.baud = ttk.Combobox(connection, state='readonly', values=('9600', '10400', '4800'), width=9)
        self.baud.set('9600')
        self.baud.grid(row=1, column=1, padx=12)
        self.refresh_button = ttk.Button(connection, text='Atualizar portas', command=self.refresh)
        self.refresh_button.grid(row=1, column=2)
        connection.columnconfigure(0, weight=1)
        actions = ttk.Frame(body)
        actions.pack(fill='x', pady=(8, 18))
        self.connect_button = ttk.Button(actions, text='Identificar ECU', command=self.connect, style='Accent.TButton')
        self.connect_button.pack(side='left')
        self.stop_button = ttk.Button(actions, text='Desligar', command=self.disconnect, state='disabled')
        self.stop_button.pack(side='left', padx=8)
        ttk.Button(actions, text='Guardar trace…', command=self.export).pack(side='right')
        self.status = tk.StringVar(value='Seleciona a porta do cabo para começar.')
        self.identity = tk.StringVar(value='ECU não identificada')
        ttk.Label(body, textvariable=self.identity, font=('Segoe UI', 14, 'bold')).pack(anchor='w')
        ttk.Label(body, textvariable=self.status, wraplength=850).pack(anchor='w', pady=(5, 16))
        reads = ttk.Frame(body)
        reads.pack(fill='x')
        ttk.Label(reads, text='Grupo').pack(side='left')
        self.group = ttk.Combobox(reads, state='readonly', values=('001', '003', '011'), width=7)
        self.group.set('001')
        self.group.pack(side='left', padx=8)
        self.read_button = ttk.Button(reads, text='Ler uma vez', command=self.read, state='disabled')
        self.read_button.pack(side='left')
        self.monitor_button = ttk.Button(reads, text='Iniciar monitor', command=self.monitor, state='disabled')
        self.monitor_button.pack(side='left', padx=8)
        self.build_cockpit(body)
        ttk.Label(body, text='Respostas da ECU · bytes brutos, sem conversão de sensores').pack(anchor='w', pady=(8, 6))
        self.table = ttk.Treeview(body, columns=('group', 'data'), show='headings', height=3)
        self.table.heading('group', text='Grupo')
        self.table.heading('data', text='Resposta')
        self.table.column('group', width=100, stretch=False)
        self.table.column('data', width=650)
        self.table.pack(fill='both', expand=True)
        for group in ('001', '003', '011'):
            self.table.insert('', 'end', iid=group, values=(group, 'Sem leitura'))
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.report_callback_exception = lambda typ, value, tb: messagebox.showerror('FIS AFN', str(value), parent=root)
        self.refresh()
        self.poll_id = root.after(150, self.poll)

    def build_cockpit(self, body):
        """Keep the original cockpit identity without inventing decoded readings."""
        dashboard = ttk.Frame(body)
        dashboard.pack(fill='x', pady=(18, 0))
        dashboard.columnconfigure(0, weight=3)
        dashboard.columnconfigure(1, weight=2)

        def panel(parent, title, unit, column):
            frame = tk.Frame(parent, bg='#1b2124', highlightbackground='#384144', highlightthickness=1)
            frame.grid(row=0, column=column, sticky='nsew', padx=(0, 10) if column == 0 else 0)
            tk.Label(frame, text=title, bg='#1b2124', fg='#e3e7e5', font=('Segoe UI', 14, 'bold')).pack(anchor='w', padx=18, pady=(12, 0))
            line = tk.Frame(frame, bg='#1b2124')
            line.pack(fill='x', padx=18)
            tk.Label(line, text='—', bg='#1b2124', fg='#df3442' if column == 0 else '#dce3df', font=('Segoe UI', 36, 'bold')).pack(side='left')
            tk.Label(line, text=unit, bg='#1b2124', fg='#aab5b7', font=('Segoe UI', 11)).pack(side='left', padx=12)
            tk.Label(frame, text='Pedido —    /    Desvio —' if column == 0 else 'Sem medição validada', bg='#1b2124', fg='#aab5b7', font=('Segoe UI', 10)).pack(anchor='w', padx=18, pady=(0, 12))
            return frame

        panel(dashboard, 'Pressão do turbo', 'bar absolutos', 0)
        panel(dashboard, 'Regime do motor', 'rpm', 1)
        metrics = ttk.Frame(body)
        metrics.pack(fill='x', pady=(10, 0))
        for column, (title, unit) in enumerate((('Massa de ar', 'mg/str'), ('Quantidade injetada', 'mg/str'), ('Temperatura', '°C'), ('N75', '%'))):
            metrics.columnconfigure(column, weight=1, uniform='metric')
            frame = tk.Frame(metrics, bg='#1b2124', highlightbackground='#384144', highlightthickness=1)
            frame.grid(row=0, column=column, sticky='ew', padx=(0, 10) if column < 3 else 0)
            tk.Label(frame, text=title, bg='#1b2124', fg='#aab5b7', font=('Segoe UI', 10)).pack(anchor='w', padx=14, pady=(9, 0))
            tk.Label(frame, text=f'—  {unit}', bg='#1b2124', fg='#dce3df', font=('Segoe UI', 18)).pack(anchor='w', padx=14, pady=(0, 9))
        ttk.Label(body, text='Indicadores à espera de descodificação validada. As respostas reais aparecem abaixo.', style='Muted.TLabel').pack(anchor='w', pady=(9, 0))

    def refresh(self):
        try:
            current = self.port.get()
            self.ports = {f'{p.device} — {p.description}': p.device for p in self.ports_factory()}
            self.port['values'] = tuple(self.ports)
            self.port.set(current if current in self.ports else next(iter(self.ports), ''))
            if not self.ports:
                self.status.set('Nenhuma porta encontrada. Liga o cabo USB e atualiza as portas.')
        except Exception as exc:
            self.status.set(str(exc))

    def connect(self):
        device = self.ports.get(self.port.get())
        if not device:
            self.status.set('Seleciona a porta do cabo.')
            return
        self.disconnect()
        baud = int(self.baud.get())
        self.trace = DiagnosticTrace()
        self.trace.begin_session(device, baud, local_echo=True)
        self.source = self.source_factory(device, baudrate=baud, trace=self.trace, groups=())
        for group in ('001', '003', '011'):
            self.table.item(group, values=(group, 'Sem leitura'))
        self.source.start()
        self.status.set('A identificar a ECU…')
        self.controls(False, True)

    def controls(self, identified, active, monitoring=False):
        self.connect_button['state'] = 'disabled' if active else 'normal'
        self.stop_button['state'] = 'normal' if active else 'disabled'
        self.port['state'] = 'disabled' if active else 'readonly'
        self.baud['state'] = 'disabled' if active else 'readonly'
        self.refresh_button['state'] = 'disabled' if active else 'normal'
        self.read_button['state'] = 'normal' if identified and not monitoring else 'disabled'
        self.monitor_button['state'] = 'normal' if identified else 'disabled'
        self.monitor_button['text'] = 'Parar monitor' if monitoring else 'Iniciar monitor'

    def read(self):
        if self.source:
            self.source.set_groups((int(self.group.get()),))

    def monitor(self):
        if self.source:
            if self.source.snapshot()['monitor']['active']:
                self.source.stop_monitor()
            else:
                self.source.start_monitor((int(self.group.get()),), min_period_s=1.0)

    def poll(self):
        if self.closed:
            return
        if self.source:
            state = self.source.snapshot()
            ecu = state.get('ecu') or {}
            self.identity.set(' · '.join(str(ecu.get(k)) for k in ('part_number', 'component') if ecu.get(k)) or 'ECU não identificada')
            names = {'connecting': 'A abrir o cabo…', 'identifying': 'A identificar a ECU…', 'identified': 'ECU identificada', 'live': 'Leitura recebida', 'error': 'Falha de ligação', 'stopped': 'Desligado', 'retrying': 'A repetir a ligação…'}
            error = state.get('error')
            self.status.set(f"{names.get(state['status'], state['status'])}" + (f' — {error}' if error else ''))
            identified = bool(ecu and state.get('session_active'))
            active = bool(state.get('session_active')) or state['status'] not in ('error', 'stopped', 'idle')
            self.controls(identified, active, state.get('monitor', {}).get('active', False))
            for group in ('001', '003', '011'):
                raw = state.get('raw_values', {}).get(f'group_{group}_raw')
                error = state.get('group_errors', {}).get(str(int(group)))
                text = str(error) if error else (' '.join(raw[i:i+2] for i in range(0, len(raw), 2)).upper() if raw else 'Sem leitura')
                self.table.item(group, values=(group, text))
        self.poll_id = self.root.after(150, self.poll)

    def disconnect(self):
        source, self.source = self.source, None
        if source:
            source.stop()
        self.identity.set('ECU não identificada')
        self.status.set('Desligado')
        self.controls(False, False)
        for group in ('001', '003', '011'):
            self.table.item(group, values=(group, 'Sem leitura'))

    def export(self):
        path = filedialog.asksaveasfilename(parent=self.root, title='Guardar trace técnico', defaultextension='.json', filetypes=[('Trace JSON', '*.json')])
        if path:
            Path(path).write_text(self.trace.export_json(), encoding='utf-8')

    def close(self):
        self.closed = True
        self.root.after_cancel(self.poll_id)
        self.disconnect()
        self.root.destroy()


def main():
    root = tk.Tk()
    DesktopApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
