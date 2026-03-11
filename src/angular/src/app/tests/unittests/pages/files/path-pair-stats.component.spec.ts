import {ComponentFixture, TestBed, fakeAsync, tick} from "@angular/core/testing";
import * as Immutable from "immutable";
import {BehaviorSubject} from "rxjs/Rx";

import {FileSizePipe} from "../../../../common/file-size.pipe";
import {PathPairStatsComponent} from "../../../../pages/files/path-pair-stats.component";
import {ViewFile} from "../../../../services/files/view-file";
import {ViewFileService} from "../../../../services/files/view-file.service";
import {PathPairService} from "../../../../services/settings/path-pair.service";


class MockViewFileService {
    private readonly _files = new BehaviorSubject(Immutable.List<ViewFile>());

    get files() {
        return this._files.asObservable();
    }

    setFiles(files: ViewFile[]) {
        this._files.next(Immutable.List<ViewFile>(files));
    }
}

class MockPathPairService {
    private readonly _pathPairs = new BehaviorSubject([]);

    public pathPairs$ = this._pathPairs.asObservable();

    setPathPairs(pathPairs: any[]) {
        this._pathPairs.next(pathPairs);
    }
}

function createViewFile(props): ViewFile {
    return new ViewFile({
        fileId: props.fileId || null,
        pathPairId: props.pathPairId || null,
        pathPairName: props.pathPairName || null,
        name: props.name || "file.mkv",
        isDir: false,
        localSize: props.localSize || 0,
        remoteSize: props.remoteSize || 0,
        percentDownloaded: 0,
        status: props.status || ViewFile.Status.DEFAULT,
        downloadingSpeed: props.downloadingSpeed || 0,
        eta: 0,
        fullPath: "/downloads/" + (props.name || "file.mkv"),
        isArchive: false,
        isSelected: false,
        isQueueable: true,
        isStoppable: false,
        isExtractable: false,
        isLocallyDeletable: true,
        isRemotelyDeletable: true,
        localCreatedTimestamp: null,
        localModifiedTimestamp: null,
        remoteCreatedTimestamp: null,
        remoteModifiedTimestamp: null
    });
}

function createPathPair(id: string, name: string, enabled = true) {
    return {
        id: id,
        name: name,
        remote_path: "/remote/" + name.toLowerCase(),
        local_path: "/downloads/" + name.toLowerCase(),
        enabled: enabled,
        auto_queue: true
    };
}

describe("Testing path-pair stats component", () => {
    let fixture: ComponentFixture<PathPairStatsComponent>;
    let component: PathPairStatsComponent;
    let viewFileService: MockViewFileService;
    let pathPairService: MockPathPairService;

    beforeEach(() => {
        viewFileService = new MockViewFileService();
        pathPairService = new MockPathPairService();

        TestBed.configureTestingModule({
            declarations: [
                PathPairStatsComponent,
                FileSizePipe
            ],
            providers: [
                {provide: ViewFileService, useValue: viewFileService},
                {provide: PathPairService, useValue: pathPairService}
            ]
        });

        fixture = TestBed.createComponent(PathPairStatsComponent);
        component = fixture.componentInstance;
    });

    it("should create an instance", () => {
        expect(component).toBeDefined();
    });

    it("should calculate grouped stats for enabled path pairs", fakeAsync(() => {
        pathPairService.setPathPairs([
            createPathPair("movies", "Movies"),
            createPathPair("tv", "TV"),
            createPathPair("disabled", "Disabled", false)
        ]);
        viewFileService.setFiles([
            createViewFile({
                name: "movie1.mkv",
                pathPairId: "movies",
                pathPairName: "Movies",
                localSize: 500,
                remoteSize: 1000,
                status: ViewFile.Status.DOWNLOADING,
                downloadingSpeed: 250
            }),
            createViewFile({
                name: "movie2.mkv",
                pathPairId: "movies",
                pathPairName: "Movies",
                localSize: 1000,
                remoteSize: 1000,
                status: ViewFile.Status.DOWNLOADED
            }),
            createViewFile({
                name: "show1.mkv",
                pathPairId: "tv",
                pathPairName: "TV",
                localSize: 0,
                remoteSize: 500,
                status: ViewFile.Status.QUEUED
            }),
            createViewFile({
                name: "hidden.mkv",
                pathPairId: "disabled",
                pathPairName: "Disabled",
                localSize: 100,
                remoteSize: 100,
                status: ViewFile.Status.DOWNLOADED
            })
        ]);

        fixture.detectChanges();
        tick();
        fixture.detectChanges();

        expect(component.hasMultiplePathPairs).toBe(true);
        expect(component.stats.length).toBe(2);

        expect(component.stats[0].pathPairId).toBe("movies");
        expect(component.stats[0].totalFiles).toBe(2);
        expect(component.stats[0].downloadingCount).toBe(1);
        expect(component.stats[0].downloadedCount).toBe(1);
        expect(component.stats[0].totalSpeed).toBe(250);
        expect(component.stats[0].overallProgress).toBe(75);

        expect(component.stats[1].pathPairId).toBe("tv");
        expect(component.stats[1].queuedCount).toBe(1);

        const cards = fixture.nativeElement.querySelectorAll(".path-pair-card");
        expect(cards.length).toBe(2);
    }));

    it("should hide the container when fewer than two enabled path pairs exist", fakeAsync(() => {
        pathPairService.setPathPairs([
            createPathPair("movies", "Movies")
        ]);
        viewFileService.setFiles([
            createViewFile({
                name: "movie1.mkv",
                pathPairId: "movies",
                pathPairName: "Movies",
                localSize: 100,
                remoteSize: 100
            })
        ]);

        fixture.detectChanges();
        tick();
        fixture.detectChanges();

        expect(component.hasMultiplePathPairs).toBe(false);
        expect(fixture.nativeElement.querySelector(".path-pair-stats-container")).toBeNull();
    }));

    it("should toggle the expanded state", fakeAsync(() => {
        pathPairService.setPathPairs([
            createPathPair("movies", "Movies"),
            createPathPair("tv", "TV")
        ]);
        viewFileService.setFiles([
            createViewFile({pathPairId: "movies", pathPairName: "Movies", localSize: 1, remoteSize: 1}),
            createViewFile({pathPairId: "tv", pathPairName: "TV", localSize: 1, remoteSize: 1})
        ]);

        fixture.detectChanges();
        tick();
        fixture.detectChanges();

        expect(component.isExpanded).toBe(true);
        component.toggleExpanded();
        fixture.detectChanges();

        expect(component.isExpanded).toBe(false);
        expect(fixture.nativeElement.querySelector(".stats-grid")).toBeNull();
    }));
});
