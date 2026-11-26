"""Upload ready MP4s with a title and description supplied in Google Drive.

The filename (without .mp4) is the title. A file is ready only when its
Drive description begins with READY_FOR_YOUTUBE on its own line, followed
by the exact YouTube description. Process at most one file per run.
"""
import logging
import os
import tempfile

import config
import drive_utils
import youtube_upload

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("direct_upload")
READY = "READY_FOR_YOUTUBE\n"


def main():
    if not config.DRIVE_INCOMING_FOLDER_ID or not config.DRIVE_PROCESSED_FOLDER_ID:
        raise RuntimeError("Set both Incoming and Processed Drive folder IDs.")
    drive = drive_utils.get_drive_service()
    videos = drive_utils.list_new_videos(drive)
    candidate = None
    for video in videos:
        if not video["name"].lower().endswith(".mp4"):
            log.info("Skipping non-MP4 file: %s", video["name"])
            continue
        info = drive.files().get(
            fileId=video["id"], fields="id,name,description,appProperties"
        ).execute()
        props = info.get("appProperties", {})
        state = props.get("direct_upload_state", "")
        if state == "uploading":
            raise RuntimeError(
                "Upload outcome uncertain for %s. Check YouTube Studio before any retry."
                % info["name"]
            )
        if state == "uploaded":
            candidate = (info, props, None, None)
            break
        if state:
            raise RuntimeError("Unknown upload state for %s: %s" % (info["name"], state))
        description = info.get("description", "").replace("\r\n", "\n")
        if not description.startswith(READY):
            log.info("Not marked ready; skipping: %s", info["name"])
            continue
        description = description[len(READY):].strip()
        title = os.path.splitext(info["name"])[0].strip()
        if not title or len(title) > 100 or "<" in title or ">" in title:
            raise RuntimeError("Invalid YouTube title for %s (max 100 characters)." % info["name"])
        if not description or len(description) > 5000 or "<" in description or ">" in description:
            raise RuntimeError("Invalid description for %s (max 5000 characters)." % info["name"])
        candidate = (info, props, title, description)
        break

    if not candidate:
        log.info("No ready videos found; no upload made.")
        return
    info, props, title, description = candidate
    file_id = info["id"]
    log.info("Selected file: %s; title: %s; state: %s", info["name"], title, props.get("direct_upload_state", ""))
    if config.DRY_RUN_LOG_ONLY:
        log.info("DRY RUN: no upload or Drive changes made.")
        return

    if props.get("direct_upload_state") == "uploaded":
        log.info("Already uploaded; moving to Processed: %s", props.get("direct_upload_url", ""))
    else:
        youtube_upload.validate_youtube_credentials()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "video.mp4")
            drive_utils.download_file(drive, file_id, path)
            # Claim before upload; never automatically retry an uncertain outcome.
            drive.files().update(
                fileId=file_id,
                body={"appProperties": {"direct_upload_state": "uploading"}},
            ).execute()
            url = youtube_upload.upload_short(path, title, description, tags=["shorts", "gaming"])
            drive.files().update(
                fileId=file_id,
                body={"appProperties": {
                    "direct_upload_state": "uploaded", "direct_upload_url": url,
                }},
            ).execute()
            log.info("Uploaded: %s", url)

    drive_utils.move_file(
        drive, file_id, config.DRIVE_INCOMING_FOLDER_ID, config.DRIVE_PROCESSED_FOLDER_ID
    )
    log.info("Moved to Processed.")


if __name__ == "__main__":
    main()
