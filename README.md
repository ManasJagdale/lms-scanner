# BITS Hyderabad LMS Assessment Pipeline

A local workflow for collecting course announcements and materials from the BITS Hyderabad Moodle LMS, preparing assessment-focused bundles for manual review with Claude.ai, and exporting results to CSV and JSON.

This repository contains reusable code and documentation only. It does not include an LMS token, course dump, downloaded course files, generated bundles, or Claude replies.

## What it does

1. Fetches enrolled courses, sections, material links, and announcements from the LMS.
2. Downloads course material files to a local folder.
3. Selects recent assessment-related announcements and course documents, extracts readable text, and divides them into JSON bundles.
4. You choose whether to send each bundle to Claude.ai, then save its reply in that run folder.
5. Validates replies against item IDs in that run's bundles, merges duplicate assessment records, and exports local CSV and JSON files.

The scripts do not call an AI API. You manually decide whether to upload bundle content to Claude.ai. Bundles can contain course announcements, file names, and extracted course document text, so inspect them before uploading.

## Requirements

- Windows and PowerShell
- Python 3.10 or newer
- The Python packages listed in requirements.txt

From this folder, create a virtual environment and install the dependencies:

    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    pip install -r requirements.txt

## Run the pipeline

Optional: check the LMS endpoints with dummy credentials:

    python lms_probe.py

Fetch a local course snapshot, download materials, and create a timestamped run folder:

    .\run_lms.ps1 refresh

The first fetch prompts for your LMS username and password, or for a token from the LMS single sign-on flow. The password is not saved. The access token is cached in lms_token.txt for later requests. Treat that file like a password; it is ignored by Git.

By default, downloaded materials go under LMS_Files in this folder. To use a different location:

    .\run_lms.ps1 refresh -Root "D:\LMS_Files"

To include documents whose names match an extra regular expression:

    .\run_lms.ps1 refresh -Also "sunny|handout"

Each run creates a timestamped folder under runs. It contains lms_bundle_NN.json files, lms_prompt.txt, and lms_manual_attach.txt. The last file lists documents that could not be read as text. Attach those documents manually only if you want Claude to consider them.

In a new Claude.ai chat, attach each bundle, paste the contents of lms_prompt.txt, and save each reply in the same run folder as claude_output_01.json, claude_output_02.json, and so on. Keep each reply as a JSON array, either in a code block or as plain JSON.

Preview and export the results after saving the replies:

    .\run_lms.ps1 review -RunDir ".\runs\YYYYMMDD-HHMMSS"

The review step accepts only item IDs in that run's bundles. It merges duplicates, hides past and undated records by default, and writes assessments.csv and assessments.json into the run folder. Rows replaced by a later reschedule are hidden too, and cancelled assessments stay listed with change = cancelled. Use --include-past, --keep-undated or --keep-superseded with lms_review.py (or -IncludePast, -KeepUndated, -KeepSuperseded with run_lms.ps1) to include hidden records. The review step warns if a reply skips any bundle item. Text cells beginning with =, +, - or @ are prefixed with an apostrophe in the CSV so spreadsheets do not run them as formulas.

## Configuration

The default LMS address is https://lms.bits-hyderabad.ac.in. To override it in the current PowerShell session:

    $env:LMS_BASE_URL = "https://your-lms.example"

The assessment filters and bundle size can be adjusted in lms_bundle.py. Defaults include announcements from the last 60 days, matching documents modified in the last 120 days, and at most 40 documents.

## Keep private data out of Git

The repository ignores the token, fetched dump, file manifest, downloaded materials, run folders, bundles, and Claude replies. Do not force-add those files. Review git status before committing or pushing.
