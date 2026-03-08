import {Injectable} from "@angular/core";

import {LoggerService} from "../utils/logger.service";
import {ViewFile} from "./view-file";
import {ViewFileComparator, ViewFileService} from "./view-file.service";
import {ViewFileOptionsService} from "./view-file-options.service";
import {ViewFileOptions} from "./view-file-options";


/**
 * Comparator used to sort the ViewFiles
 * First, sorts by status.
 * Second, sorts by name.
 * @param {ViewFile} a
 * @param {ViewFile} b
 * @returns {number}
 * @private
 */
const StatusComparator: ViewFileComparator = (a: ViewFile, b: ViewFile): number => {
    const statusComparison = compareStatus(a, b);
    if (statusComparison !== 0) {
        return statusComparison;
    }
    return compareByName(a, b);
};

/**
 * Comparator used to sort the ViewFiles
 * First, sorts by status descending.
 * Second, sorts by name.
 * @param {ViewFile} a
 * @param {ViewFile} b
 * @returns {number}
 * @private
 */
const StatusDescendingComparator: ViewFileComparator = (a: ViewFile, b: ViewFile): number => {
    const statusComparison = compareStatus(a, b);
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
    const sizeComparison = compareNullableNumbers(getSortSize(a), getSortSize(b));
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
    const sizeComparison = compareNullableNumbersDescending(getSortSize(a), getSortSize(b));
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

const compareNullableNumbers = (a: number, b: number): number => {
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

const compareNullableNumbersDescending = (a: number, b: number): number => {
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

const normalizeSortNumber = (value: number): number => {
    if (typeof value !== "number" || !isFinite(value)) {
        return null;
    }
    return value;
};

const getSortSize = (file: ViewFile): number => {
    const remoteSize = normalizeSortNumber(file.remoteSize);
    if (remoteSize !== null) {
        return remoteSize;
    }
    return normalizeSortNumber(file.localSize);
};

const compareStatus = (a: ViewFile, b: ViewFile): number => {
    if (a.status !== b.status) {
        const statusPriorities = {
            [ViewFile.Status.EXTRACTING]: 0,
            [ViewFile.Status.DOWNLOADING]: 1,
            [ViewFile.Status.QUEUED]: 2,
            [ViewFile.Status.EXTRACTED]: 3,
            [ViewFile.Status.DOWNLOADED]: 4,
            [ViewFile.Status.STOPPED]: 5,
            [ViewFile.Status.DEFAULT]: 6,
            [ViewFile.Status.DELETED]: 6  // intermix deleted and default
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
    private _sortMethod: ViewFileOptions.SortMethod = null;
    private readonly _comparators = {
        [ViewFileOptions.SortMethod.STATUS]: {
            comparator: StatusComparator,
            label: "Status"
        },
        [ViewFileOptions.SortMethod.STATUS_DESC]: {
            comparator: StatusDescendingComparator,
            label: "Status Desc"
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
            // Check if the sort method changed
            if (this._sortMethod !== options.sortMethod) {
                this._sortMethod = options.sortMethod;
                const sortConfig = this._comparators[this._sortMethod];
                if (sortConfig != null) {
                    this._viewFileService.setComparator(sortConfig.comparator);
                    this._logger.debug("Comparator set to: " + sortConfig.label);
                } else {
                    this._viewFileService.setComparator(null);
                    this._logger.debug("Comparator set to: null");
                }
            }
        });
    }
}
