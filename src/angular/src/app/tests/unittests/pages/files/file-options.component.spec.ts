import {CommonModule} from "@angular/common";
import {ComponentFixture, TestBed} from "@angular/core/testing";
import {FormsModule} from "@angular/forms";
import {BehaviorSubject} from "rxjs";

import * as Immutable from "immutable";

import {FileOptionsComponent} from "../../../../pages/files/file-options.component";
import {ViewFile} from "../../../../services/files/view-file";
import {ViewFileOptions} from "../../../../services/files/view-file-options";
import {ViewFileOptionsService} from "../../../../services/files/view-file-options.service";
import {ViewFileService} from "../../../../services/files/view-file.service";
import {DomService} from "../../../../services/utils/dom.service";


class MockViewFileOptionsService {
    private _options = new BehaviorSubject(new ViewFileOptions({
        showDetails: false,
        sortMethod: ViewFileOptions.SortMethod.SMART_STATUS,
        selectedStatusFilter: null,
        nameFilter: null,
        pinFilter: false
    }));

    get options() {
        return this._options.asObservable();
    }

    public emitOptions(options: ViewFileOptions) {
        this._options.next(options);
    }

    public setNameFilter(_name: string) {}
    public setSelectedStatusFilter(_status: ViewFile.Status) {}
    public setSortMethod(_sortMethod: ViewFileOptions.SortMethod) {}
    public setShowDetails(_show: boolean) {}
    public setPinFilter(_pinned: boolean) {}
}

class MockViewFileService {
    private _files = new BehaviorSubject(Immutable.List<ViewFile>());

    get files() {
        return this._files.asObservable();
    }

    public emitFiles(files: Immutable.List<ViewFile>) {
        this._files.next(files);
    }
}

class MockDomService {
    private _headerHeight = new BehaviorSubject(0);

    get headerHeight() {
        return this._headerHeight.asObservable();
    }
}

describe("Testing file options component", () => {
    let fixture: ComponentFixture<FileOptionsComponent>;
    let component: FileOptionsComponent;
    let viewFileOptionsService: MockViewFileOptionsService;
    let viewFileService: MockViewFileService;

    beforeEach(() => {
        TestBed.configureTestingModule({
            declarations: [FileOptionsComponent],
            imports: [
                CommonModule,
                FormsModule
            ],
            providers: [
                {provide: ViewFileOptionsService, useClass: MockViewFileOptionsService},
                {provide: ViewFileService, useClass: MockViewFileService},
                {provide: DomService, useClass: MockDomService}
            ]
        });

        fixture = TestBed.createComponent(FileOptionsComponent);
        component = fixture.componentInstance;
        viewFileOptionsService = TestBed.get(ViewFileOptionsService) as any;
        viewFileService = TestBed.get(ViewFileService) as any;
        fixture.detectChanges();
    });

    afterEach(() => {
        fixture.destroy();
    });

    it("should close open dropdowns on scroll", () => {
        const root = fixture.nativeElement.querySelector("#file-options");
        const dropdown = root.querySelector("#filter-status");
        const menu = dropdown.querySelector(".dropdown-menu");
        const button = dropdown.querySelector(".dropdown-toggle");

        dropdown.classList.add("show");
        menu.classList.add("show");
        button.classList.add("show");
        button.setAttribute("aria-expanded", "true");

        window.dispatchEvent(new Event("scroll"));

        expect(dropdown.classList.contains("show")).toBe(false);
        expect(menu.classList.contains("show")).toBe(false);
        expect(button.classList.contains("show")).toBe(false);
        expect(button.getAttribute("aria-expanded")).toBe("false");
    });

    it("should keep status filters in sync with the file list and current selection", () => {
        expect(component.getStatusCount(ViewFile.Status.QUEUED)).toBe(0);
        expect(component.isStatusDisabled(ViewFile.Status.QUEUED)).toBe(true);
        expect(component.isStatusDisabled(ViewFile.Status.STOPPED)).toBe(true);

        const statusButtons = Array.from(
            fixture.nativeElement.querySelectorAll("#filter-status .dropdown-menu .dropdown-item")
        ) as HTMLButtonElement[];
        const queuedButton = statusButtons.find(button => (button.textContent || "").includes("Queued"));

        expect(queuedButton).toBeDefined();
        expect(queuedButton!.disabled).toBe(true);

        viewFileService.emitFiles(Immutable.List([
            new ViewFile({status: ViewFile.Status.QUEUED}),
            new ViewFile({status: ViewFile.Status.QUEUED}),
            new ViewFile({status: ViewFile.Status.DOWNLOADING})
        ]));

        expect(component.getStatusCount(ViewFile.Status.QUEUED)).toBe(2);
        expect(component.getStatusCount(ViewFile.Status.DOWNLOADING)).toBe(1);
        expect(component.isStatusAvailable(ViewFile.Status.QUEUED)).toBe(true);
        expect(component.isStatusDisabled(ViewFile.Status.QUEUED)).toBe(false);
        expect(component.isStatusDisabled(ViewFile.Status.STOPPED)).toBe(true);

        viewFileOptionsService.emitOptions(new ViewFileOptions({
            showDetails: false,
            sortMethod: ViewFileOptions.SortMethod.SMART_STATUS,
            selectedStatusFilter: ViewFile.Status.STOPPED,
            nameFilter: null,
            pinFilter: false
        }));

        expect(component.isStatusDisabled(ViewFile.Status.STOPPED)).toBe(false);

        viewFileService.emitFiles(Immutable.List([
            new ViewFile({status: ViewFile.Status.EXTRACTED})
        ]));

        expect(component.getStatusCount(ViewFile.Status.EXTRACTED)).toBe(1);
        expect(component.getStatusCount(ViewFile.Status.QUEUED)).toBe(0);
        expect(component.getStatusCount(ViewFile.Status.DOWNLOADING)).toBe(0);
        expect(component.isStatusAvailable(ViewFile.Status.EXTRACTED)).toBe(true);
        expect(component.isStatusDisabled(ViewFile.Status.QUEUED)).toBe(true);
        expect(component.isStatusDisabled(ViewFile.Status.STOPPED)).toBe(false);
    });

    it("should delegate filter and sort changes to the service", () => {
        const setNameFilterSpy = spyOn(viewFileOptionsService, "setNameFilter");
        const setSelectedStatusFilterSpy = spyOn(viewFileOptionsService, "setSelectedStatusFilter");
        const setSortMethodSpy = spyOn(viewFileOptionsService, "setSortMethod");
        const setShowDetailsSpy = spyOn(viewFileOptionsService, "setShowDetails");
        const setPinFilterSpy = spyOn(viewFileOptionsService, "setPinFilter");

        viewFileService.emitFiles(Immutable.List([
            new ViewFile({status: ViewFile.Status.DOWNLOADED})
        ]));
        viewFileOptionsService.emitOptions(new ViewFileOptions({
            showDetails: true,
            sortMethod: ViewFileOptions.SortMethod.SMART_STATUS,
            selectedStatusFilter: null,
            nameFilter: null,
            pinFilter: true
        }));

        component.onFilterByName("queued");
        component.onFilterByStatus(ViewFile.Status.DOWNLOADED);
        component.onSort(ViewFileOptions.SortMethod.NAME_DESC);
        component.onToggleShowDetails();
        component.onTogglePinFilter();

        expect(setNameFilterSpy).toHaveBeenCalledWith("queued");
        expect(setSelectedStatusFilterSpy).toHaveBeenCalledWith(ViewFile.Status.DOWNLOADED);
        expect(setSortMethodSpy).toHaveBeenCalledWith(ViewFileOptions.SortMethod.NAME_DESC);
        expect(setShowDetailsSpy).toHaveBeenCalledWith(false);
        expect(setPinFilterSpy).toHaveBeenCalledWith(false);
    });

    it("should ignore disabled status filter selections", () => {
        const setSelectedStatusFilterSpy = spyOn(viewFileOptionsService, "setSelectedStatusFilter");

        component.onFilterByStatus(ViewFile.Status.QUEUED);

        expect(setSelectedStatusFilterSpy).not.toHaveBeenCalled();
    });

    it("should stop reacting to file and option updates after destroy", () => {
        viewFileService.emitFiles(Immutable.List([
            new ViewFile({status: ViewFile.Status.QUEUED})
        ]));
        viewFileOptionsService.emitOptions(new ViewFileOptions({
            showDetails: false,
            sortMethod: ViewFileOptions.SortMethod.SMART_STATUS,
            selectedStatusFilter: null,
            nameFilter: null,
            pinFilter: false
        }));

        expect(component.getStatusCount(ViewFile.Status.QUEUED)).toBe(1);
        expect((component as any)._latestOptions.showDetails).toBe(false);

        fixture.destroy();

        viewFileService.emitFiles(Immutable.List([
            new ViewFile({status: ViewFile.Status.QUEUED}),
            new ViewFile({status: ViewFile.Status.QUEUED})
        ]));
        viewFileOptionsService.emitOptions(new ViewFileOptions({
            showDetails: true,
            sortMethod: ViewFileOptions.SortMethod.SMART_STATUS,
            selectedStatusFilter: null,
            nameFilter: null,
            pinFilter: false
        }));

        expect(component.getStatusCount(ViewFile.Status.QUEUED)).toBe(1);
        expect((component as any)._latestOptions.showDetails).toBe(false);
    });
});
