# EP-FXT Data Downloader

A command-line tool for searching and downloading Einstein Probe Follow-up X-ray Telescope (EP-FXT) data.

The tool can:

- Search FXT observations using a source catalogue;
- Download data from an existing observation list;
- Filter observations by detector (`FXTA` or `FXTB`);
- Download user-data ZIP files and source CSV tables;
- Retry failed observations;
- Save execution logs automatically.

## Requirements

- Python 3.9+
- An Einstein Probe account
- Internet access to `ep.bao.ac.cn`
- Playwright

Install the Python dependencies:

```bash
pip install pandas requests playwright
```

Install the Playwright browser:

```bash
playwright install chromium
```

## Usage

### Download using a source catalogue

```bash
python get_fxt.py \
    --mode=source \
    --catalog=/path/to/source_list.csv \
    --detnam=FXTB \
    --output=/path/to/output \
    --username=YOUR_USERNAME
```

The password will be requested interactively and will not be displayed on screen.

### Download using an existing observation list

```bash
python get_fxt.py \
    --mode=obslist \
    --obs-list=/path/to/fxt_obs_list.csv \
    --output=/path/to/output \
    --username=YOUR_USERNAME
```

### Show the browser window

By default, Playwright runs in headless mode. To show the browser:

```bash
python get_fxt.py \
    --mode=obslist \
    --obs-list=/path/to/fxt_obs_list.csv \
    --output=/path/to/output \
    --username=YOUR_USERNAME \
    --visible
```

## Command-line options

| Option | Description | Default |
|---|---|---|
| `--mode` | `source` or `obslist` | Required |
| `--catalog` | Source catalogue CSV | None |
| `--obs-list` | Existing observation-list CSV | None |
| `--detnam` | `FXTA`, `FXTB`, or `ALL` | `ALL` |
| `--output` | Output directory | `./fxt_data` |
| `--username` | Einstein Probe username | Required |
| `--visible` | Show the browser window | Disabled |
| `--page-timeout` | Page timeout in seconds | `300` |
| `--async-timeout` | Maximum ZIP packaging wait in seconds | `300` |
| `--retries` | Number of retries per observation | `2` |

Example with custom timeout and retry settings:

```bash
python get_fxt.py \
    --mode=obslist \
    --obs-list=/path/to/fxt_obs_list.csv \
    --output=/path/to/output \
    --username=YOUR_USERNAME \
    --page-timeout=600 \
    --async-timeout=900 \
    --retries=5
```

## Input files

### Source catalogue

The source catalogue should contain the columns required by the EP-FXT API, it should contains at least:

```Source Name
Star Time
End Time
RA
DEC
Radius
```
See the source_list_example.csv, 'Radius' is the searching radius.

### Observation list

The observation list should contain at least:

```text
obs_id
detnam
version
```

See the fxt_obs_list_example.csv. `version` must be two digits; values below 10 are zero-padded. The default version is `01`.

## Output structure

For each observation, the tool creates a separate directory:

```text
output/
└── OBS_ID_FXTB_02/
    ├── OBS_ID_FXTB_02_user.zip
    └── OBS_ID_FXTB_02_sources.csv
```

A log file is also created:

```text
fxt_download_YYYYMMDD_HHMMSS.log
```

## Detector filtering

Only FXTB observations:

```bash
--detnam=FXTB
```

Only FXTA observations:

```bash
--detnam=FXTA
```

Both detectors:

```bash
--detnam=ALL
```

## Warning

Monitor your login status, as the session may expire after several hours.

Please avoid downloading many (more than 60) observations per hour to reduce the load on the website.


### User ZIP download fails

The server may report:

```text
[async] status = Download completed!
```

before providing a valid download link. The source CSV can still be downloaded independently.

