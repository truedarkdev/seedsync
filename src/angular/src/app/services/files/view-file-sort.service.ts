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
 * Second, sorts same-status files by older remote timestamps.
 * Third, sorts by name.
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
    const timestampComparison = compareRemoteTimestamp(a, b);
    if (timestampComparison !== 0) {
        return timestampComparison;
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

const compareByName = (a: ViewFile, b: ViewFile): number => {
    return a.name.localeCompare(b.name);
};

const compareRemoteTimestamp = (a: ViewFile, b: ViewFile): number => {
    const aTime = getRemoteCreatedTimestampValue(a);
    const bTime = getRemoteCreatedTimestampValue(b);
    if (aTime === bTime) {
        return 0;
    }
    return aTime - bTime;
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

const getRemoteCreatedTimestampValue = (file: ViewFile): number => {
    if (file.remoteCreatedTimestamp == null || typeof file.remoteCreatedTimestamp.getTime !== "function") {
        return 0;
    }
    const time = file.remoteCreatedTimestamp.getTime();
    if (typeof time !== "number" || !isFinite(time)) {
        return 0;
    }
    return time;
};

const compareStatusLegacy = (a: ViewFile, b: ViewFile): number => {
    if (a.status !== b.status) {
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
            [ViewFile.Status.DELETED]: 9  // intermix deleted and default
        };
        if (statusPriorities[a.status] !== statusPriorities[b.status]) {
            return statusPriorities[a.status] - statusPriorities[b.status];
        }
    }
    return 0;
};

const compareStatusImproved = (a: ViewFile, b: ViewFile): number => {
    if (a.status !== b.status) {
        const statusPriorities = {
            [ViewFile.Status.MOVE_FAILED]: -1,
            [ViewFile.Status.MOVE_SUCCEEDED]: 7,
            [ViewFile.Status.CORRUPT]: 0,
            [ViewFile.Status.EXTRACTING]: 1,
            [ViewFile.Status.VALIDATING]: 2,
            [ViewFile.Status.DOWNLOADING]: 3,
            [ViewFile.Status.QUEUED]: 4,
            [ViewFile.Status.STOPPED]: 5,
            [ViewFile.Status.DEFAULT]: 6,
            [ViewFile.Status.DELETED]: 6,
            [ViewFile.Status.EXTRACTED]: 7,
            [ViewFile.Status.VALIDATED]: 7,
            [ViewFile.Status.DOWNLOADED]: 7
        };
        if (statusPriorities[a.status] !== statusPriorities[b.status]) {
            return statusPriorities[a.status] - statusPriorities[b.status];
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
