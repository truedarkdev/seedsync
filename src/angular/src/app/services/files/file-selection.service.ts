import {Injectable} from "@angular/core";
import {BehaviorSubject} from "rxjs/Rx";
import {Observable} from "rxjs/Observable";

import * as Immutable from "immutable";

import {ViewFile} from "./view-file";


@Injectable()
export class FileSelectionService {
    private _visibleFiles = new BehaviorSubject<Immutable.List<ViewFile>>(Immutable.List<ViewFile>());
    private _selectedNames = new BehaviorSubject<Immutable.Set<string>>(Immutable.Set<string>());

    get selectedNames(): Observable<Immutable.Set<string>> {
        return this._selectedNames.asObservable();
    }

    get selectedFiles(): Observable<Immutable.List<ViewFile>> {
        return Observable.combineLatest(
            this._visibleFiles,
            this._selectedNames,
            (files: Immutable.List<ViewFile>, selectedNames: Immutable.Set<string>) =>
                files.filter(file => selectedNames.has(file.name)).toList()
        ).shareReplay(1);
    }

    get areAllVisibleSelected(): Observable<boolean> {
        return Observable.combineLatest(
            this._visibleFiles,
            this._selectedNames,
            (files: Immutable.List<ViewFile>, selectedNames: Immutable.Set<string>) =>
                files.size > 0 && files.every(file => selectedNames.has(file.name))
        ).shareReplay(1);
    }

    public setVisibleFiles(files: Immutable.List<ViewFile>) {
        this._visibleFiles.next(files);
        const visibleNames = Immutable.Set<string>(files.map(file => file.name).toArray());
        const prunedSelection = this._selectedNames.getValue().intersect(visibleNames) as Immutable.Set<string>;
        if (!Immutable.is(prunedSelection, this._selectedNames.getValue())) {
            this._selectedNames.next(prunedSelection);
        }
    }

    public toggle(file: ViewFile) {
        const selectedNames = this._selectedNames.getValue();
        const nextSelection = selectedNames.has(file.name) ?
            selectedNames.remove(file.name) :
            selectedNames.add(file.name);
        this._selectedNames.next(nextSelection as Immutable.Set<string>);
    }

    public setAllVisibleSelected(selected: boolean) {
        if (!selected) {
            this.clear();
            return;
        }

        const visibleNames = Immutable.Set<string>(
            this._visibleFiles.getValue().map(file => file.name).toArray()
        );
        this._selectedNames.next(visibleNames);
    }

    public clear() {
        this._selectedNames.next(Immutable.Set<string>());
    }
}
