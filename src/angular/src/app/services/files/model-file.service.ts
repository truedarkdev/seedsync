import {Injectable} from "@angular/core";
import {Observable} from "rxjs/Observable";
import {BehaviorSubject} from "rxjs/Rx";

import * as Immutable from "immutable";

import {LoggerService} from "../utils/logger.service";
import {ModelFile} from "./model-file";
import {BaseStreamService} from "../base/base-stream.service";
import {RestService, WebReaction} from "../utils/rest.service";


/**
 * ModelFileService class provides the store for model files
 * It implements the observable service pattern to push updates
 * as they become available.
 * The model is stored as an Immutable Map of file identity=>ModelFiles. Hence, the
 * ModelFiles have no defined order. The identity key allows more efficient
 * lookup and model diffing.
 * Reference: http://blog.angular-university.io/how-to-build-angular2
 *            -apps-using-rxjs-observable-data-services-pitfalls-to-avoid
 */
@Injectable()
export class ModelFileService extends BaseStreamService {
    private readonly EVENT_INIT = "model-init";
    private readonly EVENT_ADDED = "model-added";
    private readonly EVENT_UPDATED = "model-updated";
    private readonly EVENT_REMOVED = "model-removed";

    private _files: BehaviorSubject<Immutable.Map<string, ModelFile>> =
        new BehaviorSubject(Immutable.Map<string, ModelFile>());

    constructor(private _logger: LoggerService,
                private _restService: RestService) {
        super();
        this.registerEventName(this.EVENT_INIT);
        this.registerEventName(this.EVENT_ADDED);
        this.registerEventName(this.EVENT_UPDATED);
        this.registerEventName(this.EVENT_REMOVED);
    }

    get files(): Observable<Immutable.Map<string, ModelFile>> {
        return this._files.asObservable();
    }

    private static getFileKey(file: ModelFile): string {
        return file.file_id || file.name;
    }

    private static buildCommandUrl(action: string, file: ModelFile): string {
        const fileNameEncoded = encodeURIComponent(encodeURIComponent(file.name));
        let url: string = "/server/command/" + action + "/" + fileNameEncoded;
        if (file.file_id) {
            url += "?file_id=" + encodeURIComponent(file.file_id);
        }
        return url;
    }

    /**
     * Queue a file for download
     * @param {ModelFile} file
     * @returns {Observable<WebReaction>}
     */
    public queue(file: ModelFile): Observable<WebReaction> {
        this._logger.debug("Queue model file: " + file.name);
        const url: string = ModelFileService.buildCommandUrl("queue", file);
        return this._restService.post(url);
    }

    /**
     * Stop a file
     * @param {ModelFile} file
     * @returns {Observable<WebReaction>}
     */
    public stop(file: ModelFile): Observable<WebReaction> {
        this._logger.debug("Stop model file: " + file.name);
        const url: string = ModelFileService.buildCommandUrl("stop", file);
        return this._restService.post(url);
    }

    /**
     * Extract a file
     * @param {ModelFile} file
     * @returns {Observable<WebReaction>}
     */
    public extract(file: ModelFile): Observable<WebReaction> {
        this._logger.debug("Extract model file: " + file.name);
        const url: string = ModelFileService.buildCommandUrl("extract", file);
        return this._restService.post(url);
    }

    /**
     * Delete file locally
     * @param {ModelFile} file
     * @returns {Observable<WebReaction>}
     */
    public deleteLocal(file: ModelFile): Observable<WebReaction> {
        this._logger.debug("Delete locally model file: " + file.name);
        const url: string = ModelFileService.buildCommandUrl("delete_local", file);
        return this._restService.delete(url);
    }

    /**
     * Delete file remotely
     * @param {ModelFile} file
     * @returns {Observable<WebReaction>}
     */
    public deleteRemote(file: ModelFile): Observable<WebReaction> {
        this._logger.debug("Delete remotely model file: " + file.name);
        const url: string = ModelFileService.buildCommandUrl("delete_remote", file);
        return this._restService.delete(url);
    }

    protected onEvent(eventName: string, data: string) {
        this.parseEvent(eventName, data);
    }

    protected onConnected() {
        // nothing to do
    }

    protected onDisconnected() {
        // Update clients by clearing the model
        this._files.next(this._files.getValue().clear());
    }

    /**
     * Parse an event and update the file model
     * @param {string} name
     * @param {string} data
     */
    private parseEvent(name: string, data: string) {
        if (name === this.EVENT_INIT) {
            // Init event receives an array of ModelFiles
            let t0: number;
            let t1: number;

            t0 = performance.now();
            const parsed: [any] = JSON.parse(data);
            t1 = performance.now();
            this._logger.debug("Parsing took", (t1 - t0).toFixed(0), "ms");

            t0 = performance.now();
            const newFiles: ModelFile[] = [];
            for (const file of parsed) {
                newFiles.push(ModelFile.fromJson(file));
            }
            t1 = performance.now();
            this._logger.debug("ModelFile creation took", (t1 - t0).toFixed(0), "ms");

            // Replace the entire model
            t0 = performance.now();
            const newMap = Immutable.Map<string, ModelFile>(
                newFiles.map(value => ([ModelFileService.getFileKey(value), value]))
            );
            t1 = performance.now();
            this._logger.debug("ModelFile map creation took", (t1 - t0).toFixed(0), "ms");

            this._files.next(newMap);
            // this._logger.debug("New model: %O", this._files.getValue().toJS());
        } else if (name === this.EVENT_ADDED) {
            // Added event receives old and new ModelFiles
            // Only new file is relevant
            const parsed: {new_file: any} = JSON.parse(data);
            const file = ModelFile.fromJson(parsed.new_file);
            const fileKey = ModelFileService.getFileKey(file);
            if (this._files.getValue().has(fileKey)) {
                this._logger.error("ModelFile identity " + fileKey + " already exists");
            } else {
                this._files.next(this._files.getValue().set(fileKey, file));
                this._logger.debug("Added file: %O", file.toJS());
            }
        } else if (name === this.EVENT_REMOVED) {
            // Removed event receives old and new ModelFiles
            // Only old file is relevant
            const parsed: {old_file: any} = JSON.parse(data);
            const file = ModelFile.fromJson(parsed.old_file);
            const fileKey = ModelFileService.getFileKey(file);
            if (this._files.getValue().has(fileKey)) {
                this._files.next(this._files.getValue().remove(fileKey));
                this._logger.debug("Removed file: %O", file.toJS());
            } else {
                this._logger.error("Failed to find ModelFile identity " + fileKey);
            }
        } else if (name === this.EVENT_UPDATED) {
            // Updated event received old and new ModelFiles
            // We will only use the new one here
            const parsed: {new_file: any} = JSON.parse(data);
            const file = ModelFile.fromJson(parsed.new_file);
            const fileKey = ModelFileService.getFileKey(file);
            if (this._files.getValue().has(fileKey)) {
                this._files.next(this._files.getValue().set(fileKey, file));
                this._logger.debug("Updated file: %O", file.toJS());
            } else {
                this._logger.error("Failed to find ModelFile identity " + fileKey);
            }
        } else {
            this._logger.error("Unrecognized event:", name);
        }
    }
}
