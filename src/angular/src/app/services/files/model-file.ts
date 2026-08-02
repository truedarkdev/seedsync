import {Record, Set} from "immutable";

/**
 * Model file received from the backend
 * Note: Naming convention matches that used in the JSON
 */
interface IModelFile {
    file_id: string;
    path_pair_id: string;
    path_pair_name: string;
    name: string;
    is_dir: boolean;
    local_size: number | null;
    remote_size: number | null;
    remote_present: boolean;
    local_present: boolean;
    remote_has_transferable_content: boolean;
    transferred_size: number;
    download_progress: number;
    state: ModelFile.State;
    downloading_speed: number;
    eta: number;
    full_path: string;
    is_extractable: boolean;
    local_created_timestamp: Date;
    local_modified_timestamp: Date;
    remote_created_timestamp: Date;
    remote_modified_timestamp: Date;
    children: Set<ModelFile>;
    validation_progress: number;
    validation_error: string;
    corrupt_chunks: number[];
    is_stoppable: boolean;
    final_move_succeeded: boolean;
}

// Boiler plate code to set up an immutable class
const DefaultModelFile: IModelFile = {
    file_id: null,
    path_pair_id: null,
    path_pair_name: null,
    name: null,
    is_dir: null,
    local_size: null,
    remote_size: null,
    remote_present: false,
    local_present: false,
    remote_has_transferable_content: false,
    transferred_size: null,
    download_progress: null,
    state: null,
    downloading_speed: null,
    eta: null,
    full_path: null,
    is_extractable: null,
    local_created_timestamp: null,
    local_modified_timestamp: null,
    remote_created_timestamp: null,
    remote_modified_timestamp: null,
    children: null,
    validation_progress: null,
    validation_error: null,
    corrupt_chunks: null,
    is_stoppable: null,
    final_move_succeeded: false
};
const ModelFileRecord = Record(DefaultModelFile);

/**
 * Immutable class that implements the interface
 * Pattern inspired by: http://blog.angular-university.io/angular-2-application
 *                      -architecture-building-flux-like-apps-using-redux-and
 *                      -immutable-js-js
 */
export class ModelFile extends ModelFileRecord implements IModelFile {
    file_id: string;
    path_pair_id: string;
    path_pair_name: string;
    name: string;
    is_dir: boolean;
    local_size: number | null;
    remote_size: number | null;
    remote_present: boolean;
    local_present: boolean;
    remote_has_transferable_content: boolean;
    transferred_size: number;
    download_progress: number;
    state: ModelFile.State;
    downloading_speed: number;
    eta: number;
    full_path: string;
    is_extractable: boolean;
    local_created_timestamp: Date;
    local_modified_timestamp: Date;
    remote_created_timestamp: Date;
    remote_modified_timestamp: Date;
    children: Set<ModelFile>;
    validation_progress: number;
    validation_error: string;
    corrupt_chunks: number[];
    is_stoppable: boolean;
    final_move_succeeded: boolean;

    constructor(props) {
        const source = props != null && typeof props.toObject === "function" ? props.toObject() : props;
        const normalized = source == null ? source : {...source};
        // Explicit presence/content signals are authoritative, including
        // false. Derive compatibility values only for omitted/undefined
        // fields so a contradictory legacy size cannot override a backend
        // decision (for example an explicitly empty remote directory).
        if (normalized != null) {
            deriveLegacyPresenceAndContent(normalized);
        }
        super(normalized);
    }
}

// Additional types
export module ModelFile {
    export function fromJson(json): ModelFile {
        // New backends send explicit presence signals. Keep older persisted
        // fixtures readable by deriving one-time compatibility values from
        // raw nullable sizes and recursively parsed children.
        deriveLegacyPresenceAndContent(json);
        // Create immutable objects for children as well
        const children: ModelFile[] = [];
        for (const child of json.children || []) {
            children.push(ModelFile.fromJson(child));
        }
        json.children = Set<ModelFile>(children);

        // State mapping
        json.state = ModelFile.State[json.state.toUpperCase()];

        // Timestamps
        if (json.local_created_timestamp != null) {
            json.local_created_timestamp = new Date(1000 * +json.local_created_timestamp);
        }
        if (json.local_modified_timestamp != null) {
            json.local_modified_timestamp = new Date(1000 * +json.local_modified_timestamp);
        }
        if (json.remote_created_timestamp != null) {
            json.remote_created_timestamp = new Date(1000 * +json.remote_created_timestamp);
        }
        if (json.remote_modified_timestamp != null) {
            json.remote_modified_timestamp = new Date(1000 * +json.remote_modified_timestamp);
        }

        return new ModelFile(json);
    }

    export enum State {
        DEFAULT         = <any> "default",
        QUEUED          = <any> "queued",
        DOWNLOADING     = <any> "downloading",
        DOWNLOADED      = <any> "downloaded",
        DELETED         = <any> "deleted",
        EXTRACTING      = <any> "extracting",
        EXTRACTED       = <any> "extracted",
        VALIDATING      = <any> "validating",
        VALIDATED       = <any> "validated",
        CORRUPT         = <any> "corrupt",
        MOVE_FAILED     = <any> "move_failed"
    }
}

function fieldIsOmitted(source: any, field: string): boolean {
    return source == null || !Object.prototype.hasOwnProperty.call(source, field) ||
        source[field] === undefined;
}

function legacyChildValues(source: any): any[] {
    if (source == null || source.children == null) {
        return [];
    }
    if (typeof source.children.toArray === "function") {
        return source.children.toArray();
    }
    return Array.isArray(source.children) ? source.children : [];
}

function deriveLegacyRemoteTransferableContent(source: any): boolean {
    if (source == null) {
        return false;
    }
    if (source.is_dir === true) {
        return legacyChildValues(source).some(child => {
            if (fieldIsOmitted(child, "remote_has_transferable_content")) {
                return deriveLegacyRemoteTransferableContent(child);
            }
            return child.remote_has_transferable_content === true;
        });
    }
    if (!fieldIsOmitted(source, "remote_present")) {
        return source.remote_present === true;
    }
    return source.remote_size != null;
}

function deriveLegacyPresenceAndContent(source: any): void {
    if (source == null) {
        return;
    }
    if (fieldIsOmitted(source, "remote_present")) {
        source.remote_present = source.remote_size != null;
    }
    if (fieldIsOmitted(source, "local_present")) {
        // A zero-byte local file is still present in the legacy contract.
        source.local_present = source.local_size != null;
    }
    if (fieldIsOmitted(source, "remote_has_transferable_content")) {
        source.remote_has_transferable_content = deriveLegacyRemoteTransferableContent(source);
    }
}
