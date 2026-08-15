import {Injectable} from "@angular/core";

import {LoggerService} from "../utils/logger.service";
import {ViewFile} from "./view-file";
import {ViewFileComparator, ViewFileService} from "./view-file.service";
import {ViewFileOptionsService} from "./view-file-options.service";
import {ViewFileOptions} from "./view-file-options";


/**
 * Comparator used to sort the ViewFiles
 * First, sorts by legacy status priority.
 * Second, sorts by name.
 * @param {ViewFile} a
 * @param {ViewFile} b
 * @returns {number}
 * @private
 */
const LegacyStatusComparator: ViewFileComparator = (a: ViewFile, b: ViewFile): number => {
    const statusComparison = compareStatusLegacy(a, b);
    if (statusComparison !== 0) {
        return statusComparison;
    }
    return compareByName(a, b);
};

/**
 * Comparator used to sort the ViewFiles
 * First, sorts by smart status buckets.
 * Second, sorts rows within each bucket by newest download start.
 * Finally, sorts by name, keeping rows without a timestamp last.
 * @param {ViewFile} a
 * @param {ViewFile} b
 * @returns {number}
 * @private
 */
const SmartStatusComparator: ViewFileComparator = (a: ViewFile, b: ViewFile): number => {
    const statusComparison = compareStatusImproved(a, b);
    if (statusComparison !== 0) {
        return statusComparison;
    }
    const downloadedComparison = compareNullableNumbersDescending(
        getDownloadedTimestampValue(a),
        getDownloadedTimestampValue(b),
    );
    if (downloadedComparison !== 0) {
        return downloadedComparison;
    }
    return compareByName(a, b);
};

/**
 * Comparator used to sort the ViewFiles
 * First, sorts by legacy status descending.
 * Second, sorts by name.
 * @param {ViewFile} a
 * @param {ViewFile} b
 * @returns {number}
 * @private
 */
const LegacyStatusDescendingComparator: ViewFileComparator = (a: ViewFile, b: ViewFile): number => {
    const statusComparison = compareStatusLegacy(a, b);
    if (statusComparison !== 0) {
        return -statusComparison;
    }
    return compareByName(a, b);
};

/**
 * Comparator used to sort the ViewFiles
 * Sort by name, ascending
 * @param {ViewFile} a
 * @param {ViewFile} b
 * @returns {number}
 * @constructor
 */
const NameAscendingComparator: ViewFileComparator = (a: ViewFile, b: ViewFile): number => {
    return compareByName(a, b);
};

/**
 * Comparator used to sort the ViewFiles
 * Sort by name, descending
 * @param {ViewFile} a
 * @param {ViewFile} b
 * @returns {number}
 * @constructor
 */
const NameDescendingComparator: ViewFileComparator = (a: ViewFile, b: ViewFile): number => {
    return compareByName(b, a);
};

/**
 * Comparator used to sort the ViewFiles
 * Sort by size with a stable name fallback
 * @param {ViewFile} a
 * @param {ViewFile} b
 * @returns {number}
 * @constructor
 */
const SizeAscendingComparator: ViewFileComparator = (a: ViewFile, b: ViewFile): number => {
    const sizeComparison = compareNullableNumbers(getEffectiveSize(a), getEffectiveSize(b));
    if (sizeComparison !== 0) {
        return sizeComparison;
    }
    return compareByName(a, b);
};

/**
 * Comparator used to sort the ViewFiles
 * Sort by size descending with a stable name fallback
 * @param {ViewFile} a
 * @param {ViewFile} b
 * @returns {number}
 * @constructor
 */
const SizeDescendingComparator: ViewFileComparator = (a: ViewFile, b: ViewFile): number => {
    const sizeComparison = compareNullableNumbersDescending(getEffectiveSize(a), getEffectiveSize(b));
    if (sizeComparison !== 0) {
        return sizeComparison;
    }
    return compareByName(a, b);
};

/**
 * Comparator used to sort the ViewFiles
 * Sort by speed with a stable name fallback
 * @param {ViewFile} a
 * @param {ViewFile} b
 * @returns {number}
 * @constructor
 */
const SpeedAscendingComparator: ViewFileComparator = (a: ViewFile, b: ViewFile): number => {
    const speedComparison = compareNullableNumbers(a.downloadingSpeed, b.downloadingSpeed);
    if (speedComparison !== 0) {
        return speedComparison;
    }
    return compareByName(a, b);
};

/**
 * Comparator used to sort the ViewFiles
 * Sort by speed descending with a stable name fallback
 * @param {ViewFile} a
 * @param {ViewFile} b
 * @returns {number}
 * @constructor
 */
const SpeedDescendingComparator: ViewFileComparator = (a: ViewFile, b: ViewFile): number => {
    const speedComparison = compareNullableNumbersDescending(a.downloadingSpeed, b.downloadingSpeed);
    if (speedComparison !== 0) {
        return speedComparison;
    }
    return compareByName(a, b);
};

/**
 * Comparator used to sort the ViewFiles
 * Sort by eta with a stable name fallback
 * @param {ViewFile} a
 * @param {ViewFile} b
 * @returns {number}
 * @constructor
 */
const EtaAscendingComparator: ViewFileComparator = (a: ViewFile, b: ViewFile): number => {
    const etaComparison = compareNullableNumbers(a.eta, b.eta);
    if (etaComparison !== 0) {
        return etaComparison;
    }
    return compareByName(a, b);
};

/**
 * Comparator used to sort the ViewFiles
 * Sort by eta descending with a stable name fallback
 * @param {ViewFile} a
 * @param {ViewFile} b
 * @returns {number}
 * @constructor
 */
const EtaDescendingComparator: ViewFileComparator = (a: ViewFile, b: ViewFile): number => {
    const etaComparison = compareNullableNumbersDescending(a.eta, b.eta);
    if (etaComparison !== 0) {
        return etaComparison;
    }
    return compareByName(a, b);
};

/** Sort by download recency newest-first, keeping files without a valid timestamp last. */
const DownloadedNewestComparator: ViewFileComparator = (a: ViewFile, b: ViewFile): number => {
    const timestampComparison = compareNullableNumbersDescending(
        getDownloadedTimestampValue(a),
        getDownloadedTimestampValue(b),
    );
    if (timestampComparison !== 0) {
        return timestampComparison;
    }
    return compareByNameThenFileId(a, b);
};

/** Sort by download recency oldest-first, keeping files without a valid timestamp last. */
const DownloadedOldestComparator: ViewFileComparator = (a: ViewFile, b: ViewFile): number => {
    const timestampComparison = compareNullableNumbers(
        getDownloadedTimestampValue(a),
        getDownloadedTimestampValue(b),
    );
    if (timestampComparison !== 0) {
        return timestampComparison;
    }
    return compareByNameThenFileId(a, b);
};

const compareByName = (a: ViewFile, b: ViewFile): number => {
    return String(a.name || "").localeCompare(String(b.name || ""));
};

const compareByNameThenFileId = (a: ViewFile, b: ViewFile): number => {
    const nameComparison = compareByName(a, b);
    if (nameComparison !== 0) {
        return nameComparison;
    }
    return String(a.fileId || "").localeCompare(String(b.fileId || ""));
};

const compareNullableNumbers = (a: number | null, b: number | null): number => {
    const aNumber = normalizeSortNumber(a);
    const bNumber = normalizeSortNumber(b);
    if (aNumber === bNumber) {
        return 0;
    }
    if (aNumber === null) {
        return 1;
    }
    if (bNumber === null) {
        return -1;
    }
    return aNumber - bNumber;
};

const compareNullableNumbersDescending = (a: number | null, b: number | null): number => {
    const aNumber = normalizeSortNumber(a);
    const bNumber = normalizeSortNumber(b);
    if (aNumber === bNumber) {
        return 0;
    }
    if (aNumber === null) {
        return 1;
    }
    if (bNumber === null) {
        return -1;
    }
    return bNumber - aNumber;
};

const normalizeSortNumber = (value: number | null | undefined): number | null => {
    if (typeof value !== "number" || !isFinite(value)) {
        return null;
    }
    return value;
};

const getEffectiveSize = (file: ViewFile): number | null => {
    const remoteSize = normalizeSortNumber(file.remoteSize);
    if (remoteSize !== null && remoteSize > 0) {
        return remoteSize;
    }
    const localSize = normalizeSortNumber(file.localSize);
    if (localSize !== null && localSize > 0) {
        return localSize;
    }
    return null;
};

const getDownloadedTimestampValue = (file: ViewFile): number | null => {
    if (file.downloadedTimestamp == null || typeof file.downloadedTimestamp.getTime !== "function") {
        return null;
    }
    const time = file.downloadedTimestamp.getTime();
    if (typeof time !== "number" || !isFinite(time) || time < 0) {
        return null;
    }
    return time;
};

const compareStatusLegacy = (a: ViewFile, b: ViewFile): number => {
    // The row presentation deliberately labels every local-only item as
    // "Local Only", regardless of its persisted completion lineage. Keep
    // legacy Status sorting aligned with that visible category while leaving
    // the underlying state available for actions and Smart Status recency.
    const aStatus = a.visibleStatus;
    const bStatus = b.visibleStatus;
    if (aStatus !== bStatus) {
        // Smart Status is ordered by user relevance: active downloads first,
        // then attention states, next actions, finished work, and archive.
        const statusPriorities = {
            [ViewFile.Status.MOVE_FAILED]: -1,
            [ViewFile.Status.MOVE_SUCCEEDED]: 7,
            [ViewFile.Status.CORRUPT]: 0,
            [ViewFile.Status.EXTRACTING]: 1,
            [ViewFile.Status.VALIDATING]: 2,
            [ViewFile.Status.DOWNLOADING]: 3,
            [ViewFile.Status.QUEUED]: 4,
            [ViewFile.Status.EXTRACTED]: 5,
            [ViewFile.Status.VALIDATED]: 6,
            [ViewFile.Status.DOWNLOADED]: 7,
            [ViewFile.Status.STOPPED]: 8,
            [ViewFile.Status.DEFAULT]: 9,
            [ViewFile.Status.LOCAL_ONLY]: 9,
            [ViewFile.Status.DELETED]: 9  // intermix deleted and default
        };
        if (statusPriorities[aStatus] !== statusPriorities[bStatus]) {
            return statusPriorities[aStatus] - statusPriorities[bStatus];
        }
    }
    return 0;
};

const compareStatusImproved = (a: ViewFile, b: ViewFile): number => {
    const aStatus = a.visibleStatus;
    const bStatus = b.visibleStatus;
    if (aStatus !== bStatus) {
        const statusPriorities = {
            [ViewFile.Status.DOWNLOADING]: 1,
            [ViewFile.Status.EXTRACTING]: 2,
            [ViewFile.Status.VALIDATING]: 3,
            [ViewFile.Status.MOVE_FAILED]: 4,
            [ViewFile.Status.CORRUPT]: 5,
            [ViewFile.Status.STOPPED]: 6,
            [ViewFile.Status.QUEUED]: 7,
            [ViewFile.Status.DEFAULT]: 8,
            [ViewFile.Status.MOVE_SUCCEEDED]: 9,
            [ViewFile.Status.EXTRACTED]: 9,
            [ViewFile.Status.VALIDATED]: 9,
            [ViewFile.Status.DOWNLOADED]: 9,
            [ViewFile.Status.LOCAL_ONLY]: 10,
            [ViewFile.Status.DELETED]: 11
        };
        if (statusPriorities[aStatus] !== statusPriorities[bStatus]) {
            return statusPriorities[aStatus] - statusPriorities[bStatus];
        }
    }
    return 0;
};

/**
 * ViewFileSortService class provides sorting services for
 * view files
 *
 * This class responds to changes in the sort settings and
 * applies the appropriate comparators to the ViewFileService
 */
@Injectable()
export class ViewFileSortService {
    private _currentComparator: ViewFileComparator = null;
    private readonly _comparators = {
        [ViewFileOptions.SortMethod.SMART_STATUS]: {
            comparator: SmartStatusComparator,
            label: "Smart Status"
        },
        [ViewFileOptions.SortMethod.STATUS]: {
            comparator: LegacyStatusComparator,
            label: "Status"
        },
        [ViewFileOptions.SortMethod.STATUS_DESC]: {
            comparator: LegacyStatusDescendingComparator,
            label: "Status Reverse"
        },
        [ViewFileOptions.SortMethod.NAME_ASC]: {
            comparator: NameAscendingComparator,
            label: "Name Asc"
        },
        [ViewFileOptions.SortMethod.NAME_DESC]: {
            comparator: NameDescendingComparator,
            label: "Name Desc"
        },
        [ViewFileOptions.SortMethod.SIZE_ASC]: {
            comparator: SizeAscendingComparator,
            label: "Size Asc"
        },
        [ViewFileOptions.SortMethod.SIZE_DESC]: {
            comparator: SizeDescendingComparator,
            label: "Size Desc"
        },
        [ViewFileOptions.SortMethod.SPEED_ASC]: {
            comparator: SpeedAscendingComparator,
            label: "Speed Asc"
        },
        [ViewFileOptions.SortMethod.SPEED_DESC]: {
            comparator: SpeedDescendingComparator,
            label: "Speed Desc"
        },
        [ViewFileOptions.SortMethod.ETA_ASC]: {
            comparator: EtaAscendingComparator,
            label: "ETA Asc"
        },
        [ViewFileOptions.SortMethod.ETA_DESC]: {
            comparator: EtaDescendingComparator,
            label: "ETA Desc"
        },
        [ViewFileOptions.SortMethod.DOWNLOADED_NEWEST]: {
            comparator: DownloadedNewestComparator,
            label: "Downloaded Newest"
        },
        [ViewFileOptions.SortMethod.DOWNLOADED_OLDEST]: {
            comparator: DownloadedOldestComparator,
            label: "Downloaded Oldest"
        }
    };

    constructor(private _logger: LoggerService,
                private _viewFileService: ViewFileService,
                private _viewFileOptionsService: ViewFileOptionsService) {
        this._viewFileOptionsService.options.subscribe(options => {
            const sortConfig = this.getSortConfig(options);
            if (this._currentComparator !== sortConfig.comparator) {
                this._currentComparator = sortConfig.comparator;
                this._viewFileService.setComparator(sortConfig.comparator);
                this._logger.debug("Comparator set to: " + sortConfig.label);
            }
        });
    }

    private getSortConfig(options: ViewFileOptions): {comparator: ViewFileComparator, label: string} {
        const sortConfig = this._comparators[options.sortMethod];
        if (sortConfig != null) {
            return sortConfig;
        }

        return {
            comparator: null,
            label: "null"
        };
    }
}
