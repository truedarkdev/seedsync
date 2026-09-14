# Copyright 2017, Inderpreet Singh, All rights reserved.


class Constants:
    """
    POD class to hold shared constants
    :return:
    """
    SERVICE_NAME = "seedsync"
    MAIN_THREAD_SLEEP_INTERVAL_IN_SECS = 0.5
    MAX_LOG_SIZE_IN_BYTES = 10*1024*1024  # 10 MB
    LOG_BACKUP_COUNT = 10
    WEB_ACCESS_LOG_NAME = 'web_access'
    MIN_PERSIST_TO_FILE_INTERVAL_IN_SECS = 30
    CONTROLLER_SETUP_TIMEOUT_IN_SECS = 5
    # Authority can require a substantial scoped scan; it is independent of
    # startup setup and bounded rather than a guarantee that every scan ends.
    INITIAL_SCAN_AUTHORITY_TIMEOUT_IN_SECS = 120
    # Queue waits just long enough to receive the controller's terminal
    # callback after the authority fence, without recoupling setup timing.
    QUEUE_HTTP_CALLBACK_MARGIN_IN_SECS = 5
    QUEUE_HTTP_WAIT_TIMEOUT_IN_SECS = (
        INITIAL_SCAN_AUTHORITY_TIMEOUT_IN_SECS + QUEUE_HTTP_CALLBACK_MARGIN_IN_SECS
    )
    JSON_PRETTY_PRINT_INDENT = 4
    LFTP_TEMP_FILE_SUFFIX = ".lftp"
