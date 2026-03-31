import {Injectable} from "@angular/core";
import {BehaviorSubject, combineLatest, Observable} from "rxjs";
import {map, shareReplay} from "rxjs/operators";

import * as Immutable from "immutable";

import {ViewFile} from "./view-file";


@Injectable()
export class FileSelectionService {
    private _visibleFiles = new BehaviorSubject<Immutable.List<ViewFile>>(Immutable.List<ViewFile>());
    private _selectedFileIds = new BehaviorSubject<Immutable.Set<string>>(Immutable.Set<string>());

    private static getFileKey(file: ViewFile): string {
        return file.fileId || file.name;
    }

    get selectedNames(): Observable<Immutable.Set<string>> {
        return this.selectedFiles.pipe(
            map((files: Immutable.List<ViewFile>) => Immutable.Set<string>(files.map(file => file.name).toArray())),
            shareReplay(1)
        );
    }

    get selectedFileIds(): Observable<Immutable.Set<string>> {
        return this._selectedFileIds.asObservable();
    }

    get selectedFiles(): Observable<Immutable.List<ViewFile>> {
        return combineLatest([this._visibleFiles, this._selectedFileIds]).pipe(
            map(([files, selectedFileIds]: [Immutable.List<ViewFile>, Immutable.Set<string>]) =>
                files.filter(file => selectedFileIds.has(FileSelectionService.getFileKey(file))).toList()
            ),
            shareReplay(1)
        );
    }

    get areAllVisibleSelected(): Observable<boolean> {
        return combineLatest([this._visibleFiles, this._selectedFileIds]).pipe(
            map(([files, selectedFileIds]: [Immutable.List<ViewFile>, Immutable.Set<string>]) =>
                files.size > 0 && files.every(file => selectedFileIds.has(FileSelectionService.getFileKey(file)))
            ),
            shareReplay(1)
        );
    }

    public setVisibleFiles(files: Immutable.List<ViewFile>) {
        this._visibleFiles.next(files);
        const visibleFileIds = Immutable.Set<string>(
            files.map(file => FileSelectionService.getFileKey(file)).toArray()
        );
        const prunedSelection = this._selectedFileIds.getValue().intersect(visibleFileIds) as Immutable.Set<string>;
        if (!Immutable.is(prunedSelection, this._selectedFileIds.getValue())) {
            this._selectedFileIds.next(prunedSelection);
        }
    }

    public toggle(file: ViewFile) {
        const fileKey = FileSelectionService.getFileKey(file);
        const selectedFileIds = this._selectedFileIds.getValue();
        const nextSelection = selectedFileIds.has(fileKey) ?
            selectedFileIds.remove(fileKey) :
            selectedFileIds.add(fileKey);
        this._selectedFileIds.next(nextSelection as Immutable.Set<string>);
    }

    public setAllVisibleSelected(selected: boolean) {
        if (!selected) {
            this.clear();
            return;
        }

        const visibleFileIds = Immutable.Set<string>(
            this._visibleFiles.getValue().map(file => FileSelectionService.getFileKey(file)).toArray()
        );
        this._selectedFileIds.next(visibleFileIds);
    }

    public clear() {
        this._selectedFileIds.next(Immutable.Set<string>());
    }
}
