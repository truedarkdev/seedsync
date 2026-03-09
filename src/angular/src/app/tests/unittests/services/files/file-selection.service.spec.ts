import {TestBed} from "@angular/core/testing";

import * as Immutable from "immutable";

import {FileSelectionService} from "../../../../services/files/file-selection.service";
import {ViewFile} from "../../../../services/files/view-file";


describe("Testing file selection service", () => {
    let fileSelectionService: FileSelectionService;

    beforeEach(() => {
        TestBed.configureTestingModule({
            providers: [
                FileSelectionService
            ]
        });

        fileSelectionService = TestBed.get(FileSelectionService);
    });

    function createViewFile(name: string, fileId: string = null): ViewFile {
        return new ViewFile({
            fileId: fileId,
            name: name,
            isArchive: false,
            isQueueable: true,
            isStoppable: false,
            isExtractable: false,
            isLocallyDeletable: true,
            isRemotelyDeletable: true
        });
    }

    it("should select and clear all visible files", () => {
        const files = Immutable.List<ViewFile>([
            createViewFile("one"),
            createViewFile("two")
        ]);
        let selectedNames = Immutable.Set<string>();
        let allVisibleSelected = false;

        fileSelectionService.selectedNames.subscribe(value => selectedNames = value);
        fileSelectionService.areAllVisibleSelected.subscribe(value => allVisibleSelected = value);

        fileSelectionService.setVisibleFiles(files);
        fileSelectionService.setAllVisibleSelected(true);

        expect(selectedNames.toArray().sort()).toEqual(["one", "two"]);
        expect(allVisibleSelected).toBe(true);

        fileSelectionService.clear();
        expect(selectedNames.size).toBe(0);
        expect(allVisibleSelected).toBe(false);
    });

    it("should prune hidden files from the selection", () => {
        const one = createViewFile("one");
        const two = createViewFile("two");
        let selectedFiles = Immutable.List<ViewFile>();

        fileSelectionService.selectedFiles.subscribe(value => selectedFiles = value);

        fileSelectionService.setVisibleFiles(Immutable.List<ViewFile>([one, two]));
        fileSelectionService.toggle(one);
        fileSelectionService.toggle(two);
        fileSelectionService.setVisibleFiles(Immutable.List<ViewFile>([two]));

        expect(selectedFiles.size).toBe(1);
        expect(selectedFiles.get(0).name).toBe("two");
    });

    it("should keep duplicate names independently selected by file id", () => {
        const movies = createViewFile("dup", "[\"movies\",\"dup\"]");
        const tv = createViewFile("dup", "[\"tv\",\"dup\"]");
        let selectedFiles = Immutable.List<ViewFile>();
        let selectedFileIds = Immutable.Set<string>();

        fileSelectionService.selectedFiles.subscribe(value => selectedFiles = value);
        fileSelectionService.selectedFileIds.subscribe(value => selectedFileIds = value);

        fileSelectionService.setVisibleFiles(Immutable.List<ViewFile>([movies, tv]));
        fileSelectionService.toggle(movies);

        expect(selectedFiles.size).toBe(1);
        expect(selectedFiles.get(0).fileId).toBe("[\"movies\",\"dup\"]");
        expect(selectedFileIds.toArray()).toEqual(["[\"movies\",\"dup\"]"]);

        fileSelectionService.toggle(tv);

        expect(selectedFiles.size).toBe(2);
        expect(selectedFiles.map(file => file.fileId).toArray().sort()).toEqual([
            "[\"movies\",\"dup\"]",
            "[\"tv\",\"dup\"]"
        ]);
        expect(selectedFileIds.toArray().sort()).toEqual([
            "[\"movies\",\"dup\"]",
            "[\"tv\",\"dup\"]"
        ]);
    });
});
