import {Record} from "immutable";

/**
 * View file
 * Represents the View Model
 */
interface IViewFile {
    fileId: string;
    pathPairId: string;
    pathPairName: string;
    name: string;
    isDir: boolean;
    localSize: number;
    remoteSize: number;
    remotePresent: boolean;
    localPresent: boolean;
    remoteHasTransferableContent: boolean;
    isLocalOnly: boolean;
    transferredSize: number | null;
    displaySizeTotal: number;
    percentDownloaded: number | null;
    status: ViewFile.Status;
    downloadingSpeed: number;
    eta: number;
    fullPath: string;
    isArchive: boolean;  // corresponds to is_extractable in ModelFile
    isSelected: boolean;
    isQueueable: boolean;
    isStoppable: boolean;
    // whether file can be queued for extraction (independent of isArchive)
    isExtractable: boolean;
    isLocallyDeletable: boolean;
    isRemotelyDeletable: boolean;
    isValidatable: boolean;
    isMoveRetryable: boolean;
    // timestamps
    localCreatedTimestamp: Date;
    localModifiedTimestamp: Date;
    remoteCreatedTimestamp: Date;
    remoteModifiedTimestamp: Date;
    downloadedTimestamp: Date;
    validationProgress: number;
    validationError: string;
    corruptChunks: number[];
}

// Boiler plate code to set up an immutable class
const DefaultViewFile: IViewFile = {
    fileId: null,
    pathPairId: null,
    pathPairName: null,
    name: null,
    isDir: null,
    localSize: null,
    remoteSize: null,
    remotePresent: false,
    localPresent: false,
    remoteHasTransferableContent: false,
    isLocalOnly: false,
    transferredSize: null,
    displaySizeTotal: null,
    percentDownloaded: null,
    status: null,
    downloadingSpeed: null,
    eta: null,
    fullPath: null,
    isArchive: null,
    isSelected: null,
    isQueueable: null,
    isStoppable: null,
    isExtractable: null,
    isLocallyDeletable: null,
    isRemotelyDeletable: null,
    isValidatable: null,
    isMoveRetryable: null,
    localCreatedTimestamp: null,
    localModifiedTimestamp: null,
    remoteCreatedTimestamp: null,
    remoteModifiedTimestamp: null,
    downloadedTimestamp: null,
    validationProgress: null,
    validationError: null,
    corruptChunks: null
};
const ViewFileRecord = Record(DefaultViewFile);

/**
 * Immutable class that implements the interface
 */
export class ViewFile extends ViewFileRecord implements IViewFile {
    fileId: string;
    pathPairId: string;
    pathPairName: string;
    name: string;
    isDir: boolean;
    localSize: number;
    remoteSize: number;
    remotePresent: boolean;
    localPresent: boolean;
    remoteHasTransferableContent: boolean;
    isLocalOnly: boolean;
    transferredSize: number | null;
    displaySizeTotal: number;
    percentDownloaded: number | null;
    status: ViewFile.Status;
    downloadingSpeed: number;
    eta: number;
    // noinspection JSUnusedGlobalSymbols
    fullPath: string;
    isArchive: boolean;
    isSelected: boolean;
    isQueueable: boolean;
    isStoppable: boolean;
    isExtractable: boolean;
    isLocallyDeletable: boolean;
    isRemotelyDeletable: boolean;
    isValidatable: boolean;
    isMoveRetryable: boolean;
    localCreatedTimestamp: Date;
    localModifiedTimestamp: Date;
    remoteCreatedTimestamp: Date;
    remoteModifiedTimestamp: Date;
    downloadedTimestamp: Date;
    validationProgress: number;
    validationError: string;
    corruptChunks: number[];

    constructor(props) {
        super(props);
    }

    /** Status represented by the row label/icon rather than persisted lineage. */
    get visibleStatus(): ViewFile.Status {
        return this.isLocalOnly ? ViewFile.Status.LOCAL_ONLY : this.status;
    }
}

export module ViewFile {
    export enum Status {
        DEFAULT         = <any> "default",
        QUEUED          = <any> "queued",
        DOWNLOADING     = <any> "downloading",
        DOWNLOADED      = <any> "downloaded",
        STOPPED         = <any> "stopped",
        DELETED         = <any> "deleted",
        EXTRACTING      = <any> "extracting",
        EXTRACTED       = <any> "extracted",
        VALIDATING      = <any> "validating",
        VALIDATED       = <any> "validated",
        CORRUPT         = <any> "corrupt",
        MOVE_FAILED     = <any> "move_failed",
        MOVE_SUCCEEDED  = <any> "move_succeeded",
        LOCAL_ONLY      = <any> "local_only"
    }
}
