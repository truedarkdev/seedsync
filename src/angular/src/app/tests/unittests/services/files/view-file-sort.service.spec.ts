import {fakeAsync, TestBed, tick} from "@angular/core/testing";

import {LoggerService} from "../../../../services/utils/logger.service";
import {ViewFileSortService} from "../../../../services/files/view-file-sort.service";
import {MockViewFileService} from "../../../mocks/mock-view-file.service";
import {MockViewFileOptionsService} from "../../../mocks/mock-view-file-options.service";
import {ViewFileComparator, ViewFileService} from "../../../../services/files/view-file.service";
import {ViewFileOptionsService} from "../../../../services/files/view-file-options.service";
import {ViewFileOptions} from "../../../../services/files/view-file-options";
import {ViewFile} from "../../../../services/files/view-file";


describe("Testing view file sort service", () => {
    let viewSortService: ViewFileSortService;

    let viewFileService: MockViewFileService;
    let viewFileOptionsService: MockViewFileOptionsService;
    let sortComparator: ViewFileComparator;

    beforeEach(() => {
        sortComparator = undefined;
        TestBed.configureTestingModule({
            providers: [
                ViewFileSortService,
                LoggerService,
                {provide: ViewFileService, useClass: MockViewFileService},
                {provide: ViewFileOptionsService, useClass: MockViewFileOptionsService}
            ]
        });
        viewFileService = TestBed.get(ViewFileService);
        spyOn(viewFileService, "setComparator").and.callFake(
            value => sortComparator = value
        );

        viewFileOptionsService = TestBed.get(ViewFileOptionsService);

        viewSortService = TestBed.get(ViewFileSortService);
    });

    it("should create an instance", () => {
        expect(viewSortService).toBeDefined();
    });

    it("does not set a sort comparator by default", () => {
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(0);
        expect(sortComparator).toBeUndefined();
    });

    it("calls setComparator when sort method is changed", fakeAsync(() => {
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(0);
        viewFileOptionsService._options.next(new ViewFileOptions({
            sortMethod: ViewFileOptions.SortMethod.SMART_STATUS
        }));
        tick();
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(1);
        expect(sortComparator).not.toBeNull();
        viewFileOptionsService._options.next(new ViewFileOptions({
            sortMethod: ViewFileOptions.SortMethod.NAME_ASC
        }));
        tick();
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(2);
        expect(sortComparator).not.toBeNull();
        viewFileOptionsService._options.next(new ViewFileOptions({
            sortMethod: ViewFileOptions.SortMethod.NAME_DESC
        }));
        tick();
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(3);
        expect(sortComparator).not.toBeNull();
        viewFileOptionsService._options.next(new ViewFileOptions({
            sortMethod: ViewFileOptions.SortMethod.SIZE_ASC
        }));
        tick();
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(4);
        expect(sortComparator).not.toBeNull();
        viewFileOptionsService._options.next(new ViewFileOptions({
            sortMethod: ViewFileOptions.SortMethod.SIZE_DESC
        }));
        tick();
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(5);
        expect(sortComparator).not.toBeNull();
        viewFileOptionsService._options.next(new ViewFileOptions({
            sortMethod: ViewFileOptions.SortMethod.SPEED_ASC
        }));
        tick();
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(6);
        expect(sortComparator).not.toBeNull();
        viewFileOptionsService._options.next(new ViewFileOptions({
            sortMethod: ViewFileOptions.SortMethod.SPEED_DESC
        }));
        tick();
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(7);
        expect(sortComparator).not.toBeNull();
        viewFileOptionsService._options.next(new ViewFileOptions({
            sortMethod: ViewFileOptions.SortMethod.ETA_ASC
        }));
        tick();
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(8);
        expect(sortComparator).not.toBeNull();
        viewFileOptionsService._options.next(new ViewFileOptions({
            sortMethod: ViewFileOptions.SortMethod.ETA_DESC
        }));
        tick();
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(9);
        expect(sortComparator).not.toBeNull();
        viewFileOptionsService._options.next(new ViewFileOptions({
            sortMethod: ViewFileOptions.SortMethod.STATUS_DESC
        }));
        tick();
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(10);
        expect(sortComparator).not.toBeNull();
    }));

    it("does not call setComparator on duplicate sort methods", fakeAsync(() => {
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(0);
        viewFileOptionsService._options.next(new ViewFileOptions({
            sortMethod: ViewFileOptions.SortMethod.SMART_STATUS
        }));
        tick();
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(1);
        viewFileOptionsService._options.next(new ViewFileOptions({
            sortMethod: ViewFileOptions.SortMethod.SMART_STATUS
        }));
        tick();
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(1);
    }));

    it("does not call setComparator when a different option is changed", fakeAsync(() => {
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(0);
        viewFileOptionsService._options.next(new ViewFileOptions({
            sortMethod: ViewFileOptions.SortMethod.SMART_STATUS,
            showDetails: false,
        }));
        tick();
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(1);
        viewFileOptionsService._options.next(new ViewFileOptions({
            sortMethod: ViewFileOptions.SortMethod.SMART_STATUS,
            showDetails: true,
        }));
        tick();
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(1);
    }));

    it("correctly sorts by smart status", fakeAsync(() => {
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(0);
        viewFileOptionsService._options.next(new ViewFileOptions({
            sortMethod: ViewFileOptions.SortMethod.SMART_STATUS
        }));
        tick();
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(1);
        expect(sortComparator).not.toBeNull();

        // Check the order based on smart status buckets
        expect(sortComparator(
            new ViewFile({status: ViewFile.Status.EXTRACTING}),
            new ViewFile({status: ViewFile.Status.DOWNLOADING})
        )).toBeLessThan(0);
        expect(sortComparator(
            new ViewFile({status: ViewFile.Status.DOWNLOADING}),
            new ViewFile({status: ViewFile.Status.QUEUED})
        )).toBeLessThan(0);
        expect(sortComparator(
            new ViewFile({status: ViewFile.Status.QUEUED}),
            new ViewFile({status: ViewFile.Status.EXTRACTED})
        )).toBeLessThan(0);
        expect(sortComparator(
            new ViewFile({status: ViewFile.Status.CORRUPT}),
            new ViewFile({status: ViewFile.Status.EXTRACTING})
        )).toBeLessThan(0);
        expect(sortComparator(
            new ViewFile({status: ViewFile.Status.EXTRACTING}),
            new ViewFile({status: ViewFile.Status.VALIDATING})
        )).toBeLessThan(0);
        expect(sortComparator(
            new ViewFile({status: ViewFile.Status.VALIDATING}),
            new ViewFile({status: ViewFile.Status.DOWNLOADING})
        )).toBeLessThan(0);
        expect(sortComparator(
            new ViewFile({status: ViewFile.Status.STOPPED}),
            new ViewFile({status: ViewFile.Status.DOWNLOADED})
        )).toBeLessThan(0);
        expect(sortComparator(
            new ViewFile({status: ViewFile.Status.STOPPED}),
            new ViewFile({status: ViewFile.Status.DEFAULT})
        )).toBeLessThan(0);
        expect(sortComparator(
            new ViewFile({status: ViewFile.Status.STOPPED}),
            new ViewFile({status: ViewFile.Status.DELETED})
        )).toBeLessThan(0);
        expect(sortComparator(
            new ViewFile({status: ViewFile.Status.DEFAULT}),
            new ViewFile({status: ViewFile.Status.EXTRACTED})
        )).toBeLessThan(0);
        expect(sortComparator(
            new ViewFile({status: ViewFile.Status.DEFAULT}),
            new ViewFile({status: ViewFile.Status.VALIDATED})
        )).toBeLessThan(0);
        expect(sortComparator(
            new ViewFile({status: ViewFile.Status.DEFAULT}),
            new ViewFile({status: ViewFile.Status.DOWNLOADED})
        )).toBeLessThan(0);
        expect(sortComparator(
            new ViewFile({status: ViewFile.Status.DELETED}),
            new ViewFile({status: ViewFile.Status.EXTRACTED})
        )).toBeLessThan(0);
        expect(sortComparator(
            new ViewFile({status: ViewFile.Status.DELETED}),
            new ViewFile({status: ViewFile.Status.VALIDATED})
        )).toBeLessThan(0);
        expect(sortComparator(
            new ViewFile({status: ViewFile.Status.DELETED}),
            new ViewFile({status: ViewFile.Status.DOWNLOADED})
        )).toBeLessThan(0);

        // Default and Deleted should be intermixed
        expect(sortComparator(
            new ViewFile({status: ViewFile.Status.DEFAULT, name: ""}),
            new ViewFile({status: ViewFile.Status.DELETED, name: ""})
        )).toBe(0);

        // Completed bucket entries should be ordered by age across statuses.
        expect(sortComparator(
            new ViewFile({
                status: ViewFile.Status.EXTRACTED,
                name: "zeta",
                remoteCreatedTimestamp: new Date(1000)
            }),
            new ViewFile({
                status: ViewFile.Status.VALIDATED,
                name: "alpha",
                remoteCreatedTimestamp: new Date(2000)
            })
        )).toBeLessThan(0);

        expect(sortComparator(
            new ViewFile({
                status: ViewFile.Status.VALIDATED,
                name: "beta",
                remoteCreatedTimestamp: new Date(1000)
            }),
            new ViewFile({
                status: ViewFile.Status.DOWNLOADED,
                name: "gamma",
                remoteCreatedTimestamp: new Date(2000)
            })
        )).toBeLessThan(0);

        // Given the same status, older remote files should come first.
        expect(sortComparator(
            new ViewFile({status: ViewFile.Status.EXTRACTED}),
            new ViewFile({status: ViewFile.Status.STOPPED})
        )).toBeGreaterThan(0);

        // If same-status timestamps match, the name fallback still applies.
        expect(sortComparator(
            new ViewFile({
                status: ViewFile.Status.EXTRACTED,
                name: "alpha",
                remoteCreatedTimestamp: new Date(1000)
            }),
            new ViewFile({
                status: ViewFile.Status.EXTRACTED,
                name: "beta",
                remoteCreatedTimestamp: new Date(1000)
            })
        )).toBeLessThan(0);
    }));

    it("correctly sorts by legacy status", fakeAsync(() => {
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(0);
        viewFileOptionsService._options.next(new ViewFileOptions({
            sortMethod: ViewFileOptions.SortMethod.STATUS,
        }));
        tick();
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(1);
        expect(sortComparator).not.toBeNull();

        expect(sortComparator(
            new ViewFile({status: ViewFile.Status.EXTRACTED}),
            new ViewFile({status: ViewFile.Status.STOPPED})
        )).toBeLessThan(0);
        expect(sortComparator(
            new ViewFile({status: ViewFile.Status.EXTRACTED}),
            new ViewFile({status: ViewFile.Status.VALIDATED})
        )).toBeLessThan(0);
        expect(sortComparator(
            new ViewFile({status: ViewFile.Status.VALIDATED}),
            new ViewFile({status: ViewFile.Status.DOWNLOADED})
        )).toBeLessThan(0);
        expect(sortComparator(
            new ViewFile({status: ViewFile.Status.DOWNLOADED}),
            new ViewFile({status: ViewFile.Status.DEFAULT})
        )).toBeLessThan(0);
        expect(sortComparator(
            new ViewFile({status: ViewFile.Status.DOWNLOADED}),
            new ViewFile({status: ViewFile.Status.DELETED})
        )).toBeLessThan(0);
        expect(sortComparator(
            new ViewFile({status: ViewFile.Status.DEFAULT, name: ""}),
            new ViewFile({status: ViewFile.Status.DELETED, name: ""})
        )).toBe(0);

        expect(sortComparator(
            new ViewFile({
                status: ViewFile.Status.EXTRACTED,
                name: "zeta",
                remoteCreatedTimestamp: new Date(1000)
            }),
            new ViewFile({
                status: ViewFile.Status.EXTRACTED,
                name: "alpha",
                remoteCreatedTimestamp: new Date(2000)
            })
        )).toBeGreaterThan(0);
    }));

    it("keeps status reverse unchanged", fakeAsync(() => {
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(0);
        viewFileOptionsService._options.next(new ViewFileOptions({
            sortMethod: ViewFileOptions.SortMethod.STATUS_DESC
        }));
        tick();
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(1);
        expect(sortComparator).not.toBeNull();

        expect(sortComparator(
            new ViewFile({status: ViewFile.Status.DEFAULT, name: "alpha"}),
            new ViewFile({status: ViewFile.Status.DOWNLOADING, name: "beta"})
        )).toBeLessThan(0);
        expect(sortComparator(
            new ViewFile({status: ViewFile.Status.EXTRACTED, name: "alpha"}),
            new ViewFile({status: ViewFile.Status.EXTRACTED, name: "beta"})
        )).toBeLessThan(0);
        expect(sortComparator(
            new ViewFile({status: ViewFile.Status.VALIDATED}),
            new ViewFile({status: ViewFile.Status.EXTRACTED})
        )).toBeLessThan(0);
    }));

    it("correctly sorts by ascending name", fakeAsync(() => {
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(0);
        viewFileOptionsService._options.next(new ViewFileOptions({
            sortMethod: ViewFileOptions.SortMethod.NAME_ASC
        }));
        tick();
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(1);
        expect(sortComparator).not.toBeNull();

        expect(sortComparator(
            new ViewFile({status: ViewFile.Status.EXTRACTED, name: "flower"}),
            new ViewFile({status: ViewFile.Status.EXTRACTED, name: "tofu"})
        )).toBeLessThan(0);
        expect(sortComparator(
            new ViewFile({status: ViewFile.Status.EXTRACTED, name: "cat"}),
            new ViewFile({status: ViewFile.Status.EXTRACTED, name: "dog"})
        )).toBeLessThan(0);
        expect(sortComparator(
            new ViewFile({status: ViewFile.Status.EXTRACTED, name: "fff"}),
            new ViewFile({status: ViewFile.Status.EXTRACTED, name: "ffff"})
        )).toBeLessThan(0);
        expect(sortComparator(
            new ViewFile({status: ViewFile.Status.EXTRACTED, name: "aaaa"}),
            new ViewFile({status: ViewFile.Status.EXTRACTED, name: "aaaa"})
        )).toBe(0);
    }));

    it("correctly sorts by descending name", fakeAsync(() => {
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(0);
        viewFileOptionsService._options.next(new ViewFileOptions({
            sortMethod: ViewFileOptions.SortMethod.NAME_DESC
        }));
        tick();
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(1);
        expect(sortComparator).not.toBeNull();

        expect(sortComparator(
            new ViewFile({status: ViewFile.Status.EXTRACTED, name: "flower"}),
            new ViewFile({status: ViewFile.Status.EXTRACTED, name: "tofu"})
        )).toBeGreaterThan(0);
        expect(sortComparator(
            new ViewFile({status: ViewFile.Status.EXTRACTED, name: "cat"}),
            new ViewFile({status: ViewFile.Status.EXTRACTED, name: "dog"})
        )).toBeGreaterThan(0);
        expect(sortComparator(
            new ViewFile({status: ViewFile.Status.EXTRACTED, name: "fff"}),
            new ViewFile({status: ViewFile.Status.EXTRACTED, name: "ffff"})
        )).toBeGreaterThan(0);
        expect(sortComparator(
            new ViewFile({status: ViewFile.Status.EXTRACTED, name: "aaaa"}),
            new ViewFile({status: ViewFile.Status.EXTRACTED, name: "aaaa"})
        )).toBe(0);
    }));

    it("correctly sorts by ascending size", fakeAsync(() => {
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(0);
        viewFileOptionsService._options.next(new ViewFileOptions({
            sortMethod: ViewFileOptions.SortMethod.SIZE_ASC
        }));
        tick();
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(1);
        expect(sortComparator).not.toBeNull();

        expect(sortComparator(
            new ViewFile({name: "small", remoteSize: 10}),
            new ViewFile({name: "large", remoteSize: 100})
        )).toBeLessThan(0);
        expect(sortComparator(
            new ViewFile({name: "fallback-local", localSize: 20, remoteSize: null}),
            new ViewFile({name: "remote", remoteSize: 30})
        )).toBeLessThan(0);
        expect(sortComparator(
            new ViewFile({name: "missing"}),
            new ViewFile({name: "present", remoteSize: 30})
        )).toBeGreaterThan(0);
        expect(sortComparator(
            new ViewFile({name: "alpha", remoteSize: 100}),
            new ViewFile({name: "beta", remoteSize: 100})
        )).toBeLessThan(0);
        expect(sortComparator(
            new ViewFile({name: "alpha", remoteSize: 0, localSize: 0}),
            new ViewFile({name: "beta", remoteSize: 0, localSize: 0})
        )).toBeLessThan(0);
    }));

    it("correctly sorts by descending size", fakeAsync(() => {
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(0);
        viewFileOptionsService._options.next(new ViewFileOptions({
            sortMethod: ViewFileOptions.SortMethod.SIZE_DESC
        }));
        tick();
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(1);
        expect(sortComparator).not.toBeNull();

        expect(sortComparator(
            new ViewFile({name: "large", remoteSize: 100}),
            new ViewFile({name: "small", remoteSize: 10})
        )).toBeLessThan(0);
        expect(sortComparator(
            new ViewFile({name: "local-only", remoteSize: 0, localSize: 300}),
            new ViewFile({name: "remote", remoteSize: 200})
        )).toBeLessThan(0);
        expect(sortComparator(
            new ViewFile({name: "missing"}),
            new ViewFile({name: "present", remoteSize: 30})
        )).toBeGreaterThan(0);
        expect(sortComparator(
            new ViewFile({name: "alpha", remoteSize: 100}),
            new ViewFile({name: "beta", remoteSize: 100})
        )).toBeLessThan(0);
        expect(sortComparator(
            new ViewFile({name: "alpha", remoteSize: 0, localSize: 0}),
            new ViewFile({name: "beta", remoteSize: 0, localSize: 0})
        )).toBeLessThan(0);
    }));

    it("correctly sorts by ascending speed", fakeAsync(() => {
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(0);
        viewFileOptionsService._options.next(new ViewFileOptions({
            sortMethod: ViewFileOptions.SortMethod.SPEED_ASC
        }));
        tick();
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(1);
        expect(sortComparator).not.toBeNull();

        expect(sortComparator(
            new ViewFile({name: "slow", downloadingSpeed: 10}),
            new ViewFile({name: "fast", downloadingSpeed: 100})
        )).toBeLessThan(0);
        expect(sortComparator(
            new ViewFile({name: "missing"}),
            new ViewFile({name: "present", downloadingSpeed: 100})
        )).toBeGreaterThan(0);
    }));

    it("correctly sorts by descending speed", fakeAsync(() => {
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(0);
        viewFileOptionsService._options.next(new ViewFileOptions({
            sortMethod: ViewFileOptions.SortMethod.SPEED_DESC
        }));
        tick();
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(1);
        expect(sortComparator).not.toBeNull();

        expect(sortComparator(
            new ViewFile({name: "fast", downloadingSpeed: 100}),
            new ViewFile({name: "slow", downloadingSpeed: 10})
        )).toBeLessThan(0);
        expect(sortComparator(
            new ViewFile({name: "present", downloadingSpeed: 100}),
            new ViewFile({name: "missing"})
        )).toBeLessThan(0);
        expect(sortComparator(
            new ViewFile({name: "missing"}),
            new ViewFile({name: "present", downloadingSpeed: 100})
        )).toBeGreaterThan(0);
    }));

    it("correctly sorts by ascending eta", fakeAsync(() => {
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(0);
        viewFileOptionsService._options.next(new ViewFileOptions({
            sortMethod: ViewFileOptions.SortMethod.ETA_ASC
        }));
        tick();
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(1);
        expect(sortComparator).not.toBeNull();

        expect(sortComparator(
            new ViewFile({name: "soon", eta: 10}),
            new ViewFile({name: "later", eta: 100})
        )).toBeLessThan(0);
        expect(sortComparator(
            new ViewFile({name: "missing"}),
            new ViewFile({name: "present", eta: 100})
        )).toBeGreaterThan(0);
    }));

    it("correctly sorts by descending eta", fakeAsync(() => {
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(0);
        viewFileOptionsService._options.next(new ViewFileOptions({
            sortMethod: ViewFileOptions.SortMethod.ETA_DESC
        }));
        tick();
        expect(viewFileService.setComparator).toHaveBeenCalledTimes(1);
        expect(sortComparator).not.toBeNull();

        expect(sortComparator(
            new ViewFile({name: "later", eta: 100}),
            new ViewFile({name: "soon", eta: 10})
        )).toBeLessThan(0);
        expect(sortComparator(
            new ViewFile({name: "present", eta: 100}),
            new ViewFile({name: "missing"})
        )).toBeLessThan(0);
        expect(sortComparator(
            new ViewFile({name: "missing"}),
            new ViewFile({name: "present", eta: 100})
        )).toBeGreaterThan(0);
    }));
});
