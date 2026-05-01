# VG Config Converter

Flask web app to convert Cisco VG350 running-configs into VG410/VG420 format.
Also generates a downloadable Excel workbook with OLD_Config and NEW_Config sheets
mapping each port to its configured extension.

## Project structure

```
vg-config-converter2/
├── app.py                    <- Flask application + xlsx generation
├── requirements.txt
├── templates/
│   └── index.html            <- Jinja2 HTML template
└── static/
    ├── style.css             <- Full CSS with dark/light mode
    └── app.js                <- File drop, theme toggle, clipboard
```

## Run

```bash
cd vg-config-converter2
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000 in your browser.

## Features

- Port mapping: legacy X/Y/Z -> new 0/0/N
- Hostname and management IP replacement
- Capacity validation per target model
- Download converted config as .txt
- Download port-to-extension map as .xlsx
  - OLD_Config sheet: legacy port -> extension (sorted)
  - NEW_Config sheet: new port -> extension (sorted)
  - Excel Table format with filters, freeze panes, Notes column
