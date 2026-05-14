"""
Meeting AI — Settings Window
─────────────────────────────
Opens a Tkinter window with three tabs:
  1. LLM      — provider, model, API key
  2. Prompt   — view and edit the summarisation prompt
  3. Models   — Whisper size / language / device, speaker count
"""

import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
import yaml


CONFIG_PATH = Path(__file__).parent / 'config.yaml'

PROVIDERS   = ['nim', 'deepseek', 'groq', 'ollama', 'claude']
NIM_MODELS  = ['meta/llama-3.3-70b-instruct', 'meta/llama-3.1-8b-instruct',
               'mistralai/mixtral-8x7b-instruct-v0.1', 'custom...']
WHISPER_SIZES   = ['tiny', 'base', 'small', 'medium', 'large-v3']
WHISPER_DEVICES = ['cuda', 'cpu']
WHISPER_LANGS   = ['auto', 'zh', 'en', 'ja', 'ko', 'fr', 'de', 'es']
SPEAKER_OPTIONS = ['auto', '1', '2', '3', '4', '5', '6']


def load_cfg() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)

def save_cfg(cfg: dict):
    with open(CONFIG_PATH, 'w') as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)


def open_settings():
    """Opens the settings window. Safe to call from any thread."""
    import threading
    threading.Thread(target=_run_window, daemon=True).start()


def _run_window():
    cfg = load_cfg()

    root = tk.Tk()
    root.title('Meeting AI — Settings')
    root.resizable(False, False)
    root.attributes('-topmost', True)

    # ── Styling ───────────────────────────────────────────────────────────────
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('TNotebook.Tab', padding=[12, 6], font=('Segoe UI', 9))
    style.configure('TLabel',        font=('Segoe UI', 9))
    style.configure('TButton',       font=('Segoe UI', 9))
    style.configure('TEntry',        font=('Segoe UI', 9))
    style.configure('TCombobox',     font=('Segoe UI', 9))
    root.configure(bg='#f5f5f5')

    nb = ttk.Notebook(root)
    nb.pack(fill='both', expand=True, padx=12, pady=(12, 0))

    llm_cfg      = cfg.get('models', {}).get('llm', {})
    whisper_cfg  = cfg.get('models', {}).get('whisper', {})
    pyannote_cfg = cfg.get('models', {}).get('pyannote', {})

    # ══════════════════════════════════════════════════════════════════════════
    # Tab 1 — LLM
    # ══════════════════════════════════════════════════════════════════════════
    tab_llm = ttk.Frame(nb, padding=16)
    nb.add(tab_llm, text='  🤖  LLM  ')

    def _row(parent, label, row):
        ttk.Label(parent, text=label, foreground='#555').grid(
            row=row, column=0, sticky='w', pady=5, padx=(0, 12))

    _row(tab_llm, 'Provider', 0)
    v_provider = tk.StringVar(value=llm_cfg.get('provider', 'nim'))
    cb_provider = ttk.Combobox(tab_llm, textvariable=v_provider,
                               values=PROVIDERS, state='readonly', width=22)
    cb_provider.grid(row=0, column=1, sticky='ew')

    _row(tab_llm, 'Model', 1)
    v_model = tk.StringVar(value=llm_cfg.get('model', ''))
    e_model = ttk.Entry(tab_llm, textvariable=v_model, width=36)
    e_model.grid(row=1, column=1, sticky='ew')

    # Quick-pick model buttons for NIM
    nim_frame = ttk.Frame(tab_llm)
    nim_frame.grid(row=2, column=1, sticky='w', pady=(0, 4))
    ttk.Label(nim_frame, text='Quick pick:', foreground='#999', font=('Segoe UI', 8)
              ).pack(side='left', padx=(0, 6))
    for m in ['meta/llama-3.3-70b-instruct', 'meta/llama-3.1-8b-instruct',
              'deepseek-ai/deepseek-r1']:
        short = m.split('/')[-1][:20]
        ttk.Button(nim_frame, text=short, width=len(short)+1,
                   command=lambda val=m: v_model.set(val)
                   ).pack(side='left', padx=2)

    _row(tab_llm, 'API Key', 3)
    v_apikey = tk.StringVar(value=llm_cfg.get('api_key', ''))
    e_apikey = ttk.Entry(tab_llm, textvariable=v_apikey, width=36, show='•')
    e_apikey.grid(row=3, column=1, sticky='ew')

    def toggle_key_vis():
        e_apikey.config(show='' if e_apikey.cget('show') == '•' else '•')
    ttk.Button(tab_llm, text='👁', width=3, command=toggle_key_vis
               ).grid(row=3, column=2, padx=(4, 0))

    tab_llm.columnconfigure(1, weight=1)

    # ══════════════════════════════════════════════════════════════════════════
    # Tab 2 — Prompt
    # ══════════════════════════════════════════════════════════════════════════
    tab_prompt = ttk.Frame(nb, padding=16)
    nb.add(tab_prompt, text='  📝  Prompt  ')

    ttk.Label(tab_prompt,
              text='Customise the summarisation prompt.\n'
                   'Use {lang_instruction} and {transcript} as placeholders.',
              foreground='#666').pack(anchor='w', pady=(0, 8))

    prompt_text = tk.Text(tab_prompt, width=60, height=22,
                          font=('Consolas', 9), wrap='word',
                          relief='solid', borderwidth=1)
    prompt_text.pack(fill='both', expand=True)

    from pipeline.summarise import get_prompt_template
    prompt_text.insert('1.0', llm_cfg.get('custom_prompt', '') or get_prompt_template())

    def reset_prompt():
        from pipeline.summarise import DEFAULT_PROMPT_TEMPLATE
        prompt_text.delete('1.0', 'end')
        prompt_text.insert('1.0', DEFAULT_PROMPT_TEMPLATE)

    ttk.Button(tab_prompt, text='Reset to default', command=reset_prompt
               ).pack(anchor='e', pady=(6, 0))

    # ══════════════════════════════════════════════════════════════════════════
    # Tab 3 — Models
    # ══════════════════════════════════════════════════════════════════════════
    tab_models = ttk.Frame(nb, padding=16)
    nb.add(tab_models, text='  ⚙️  Models  ')

    def _lbl(parent, text, row, col=0):
        ttk.Label(parent, text=text, foreground='#555').grid(
            row=row, column=col, sticky='w', pady=6, padx=(0, 12))

    # Whisper section
    ttk.Label(tab_models, text='Whisper (Transcription)',
              font=('Segoe UI', 9, 'bold'), foreground='#333'
              ).grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 4))

    _lbl(tab_models, 'Model size', 1)
    v_wsize = tk.StringVar(value=whisper_cfg.get('size', 'medium'))
    ttk.Combobox(tab_models, textvariable=v_wsize, values=WHISPER_SIZES,
                 state='readonly', width=18
                 ).grid(row=1, column=1, sticky='w')

    _lbl(tab_models, 'Language', 2)
    v_wlang = tk.StringVar(value=whisper_cfg.get('language', 'auto'))
    ttk.Combobox(tab_models, textvariable=v_wlang, values=WHISPER_LANGS,
                 state='readonly', width=18
                 ).grid(row=2, column=1, sticky='w')
    ttk.Label(tab_models, text='auto = detect automatically',
              foreground='#aaa', font=('Segoe UI', 8)
              ).grid(row=2, column=2, sticky='w', padx=8)

    _lbl(tab_models, 'Device', 3)
    v_wdev = tk.StringVar(value=whisper_cfg.get('device', 'cuda'))
    ttk.Combobox(tab_models, textvariable=v_wdev, values=WHISPER_DEVICES,
                 state='readonly', width=18
                 ).grid(row=3, column=1, sticky='w')

    ttk.Separator(tab_models, orient='horizontal'
                  ).grid(row=4, column=0, columnspan=3, sticky='ew', pady=12)

    # Pyannote section
    ttk.Label(tab_models, text='Pyannote (Speaker Diarization)',
              font=('Segoe UI', 9, 'bold'), foreground='#333'
              ).grid(row=5, column=0, columnspan=2, sticky='w', pady=(0, 4))

    _lbl(tab_models, 'Speakers', 6)
    v_speakers = tk.StringVar(value=str(pyannote_cfg.get('num_speakers', 'auto')))
    ttk.Combobox(tab_models, textvariable=v_speakers, values=SPEAKER_OPTIONS,
                 state='readonly', width=18
                 ).grid(row=6, column=1, sticky='w')
    ttk.Label(tab_models, text='auto = detect automatically',
              foreground='#aaa', font=('Segoe UI', 8)
              ).grid(row=6, column=2, sticky='w', padx=8)

    # ══════════════════════════════════════════════════════════════════════════
    # Save / Cancel
    # ══════════════════════════════════════════════════════════════════════════
    btn_frame = ttk.Frame(root)
    btn_frame.pack(fill='x', padx=12, pady=10)

    def on_save():
        cfg2 = load_cfg()  # fresh read to avoid overwriting unrelated keys

        # LLM
        cfg2.setdefault('models', {}).setdefault('llm', {})
        cfg2['models']['llm']['provider'] = v_provider.get()
        cfg2['models']['llm']['model']    = v_model.get().strip()
        key = v_apikey.get().strip()
        if key:
            cfg2['models']['llm']['api_key'] = key

        # Prompt — save only if different from default
        from pipeline.summarise import DEFAULT_PROMPT_TEMPLATE, reload_config
        typed = prompt_text.get('1.0', 'end').rstrip('\n')
        cfg2['models']['llm']['custom_prompt'] = (
            '' if typed == DEFAULT_PROMPT_TEMPLATE.rstrip('\n') else typed
        )

        # Whisper
        cfg2.setdefault('models', {}).setdefault('whisper', {})
        cfg2['models']['whisper']['size']     = v_wsize.get()
        cfg2['models']['whisper']['language'] = v_wlang.get()
        cfg2['models']['whisper']['device']   = v_wdev.get()

        # Pyannote
        cfg2.setdefault('models', {}).setdefault('pyannote', {})
        ns = v_speakers.get()
        cfg2['models']['pyannote']['num_speakers'] = ns if ns == 'auto' else int(ns)

        save_cfg(cfg2)
        reload_config()   # hot-reload summarise.py config without restart
        messagebox.showinfo('Meeting AI', 'Settings saved.\nWhisper/speaker changes take effect on next recording.')
        root.destroy()

    ttk.Button(btn_frame, text='Save', command=on_save, width=10
               ).pack(side='right', padx=(6, 0))
    ttk.Button(btn_frame, text='Cancel', command=root.destroy, width=10
               ).pack(side='right')

    root.update_idletasks()
    # Centre on screen
    w, h = root.winfo_width(), root.winfo_height()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f'+{(sw-w)//2}+{(sh-h)//2}')

    root.mainloop()
