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
        sortMethod: ViewFileOptions.SortMethod.STATUS,
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

    public setNameFilter() {}
    public setSelectedStatusFilter() {}
    public setSortMethod() {}
    public setShowDetails() {}
    public setPinFilter() {}
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

    it("should stop reacting to file and option updates after destroy", () => {
        viewFileService.emitFiles(Immutable.List([
            new ViewFile({status: ViewFile.Status.QUEUED})
        ]));
        viewFileOptionsService.emitOptions(new ViewFileOptions({
            showDetails: false,
            sortMethod: ViewFileOptions.SortMethod.STATUS,
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
            sortMethod: ViewFileOptions.SortMethod.STATUS,
            selectedStatusFilter: null,
            nameFilter: null,
            pinFilter: false
        }));

        expect(component.getStatusCount(ViewFile.Status.QUEUED)).toBe(1);
        expect((component as any)._latestOptions.showDetails).toBe(false);
    });
});
