import {fakeAsync, TestBed, tick} from "@angular/core/testing";
import {Observable, of} from "rxjs";

import * as Immutable from "immutable";

import {ViewFileComparator, ViewFileService} from "../../../../services/files/view-file.service";
import {LoggerService} from "../../../../services/utils/logger.service";
import {StreamServiceRegistry} from "../../../../services/base/stream-service.registry";
import {MockStreamServiceRegistry} from "../../../mocks/mock-stream-service.registry";
import {ConnectedService} from "../../../../services/utils/connected.service";
import {MockModelFileService} from "../../../mocks/mock-model-file.service";
import {ModelFile} from "../../../../services/files/model-file";
import {ViewFile} from "../../../../services/files/view-file";
import {ViewFileFilterCriteria} from "../../../../services/files/view-file.service";
import {FileSelectionService} from "../../../../services/files/file-selection.service";
import {WebReaction} from "../../../../services/utils/rest.service";
import {LOCAL_STORAGE, StorageService} from "../../../../services/utils/storage.service";
import {MockStorageService} from "../../../mocks/mock-storage.service";
import {StorageKeys} from "../../../../common/storage-keys";


function createDuplicateNamedModelFile(fileId: string,
                                      pathPairId: string,
                                      pathPairName: string,
                                      extraProps: object = {}): ModelFile {
    return new ModelFile(Object.assign({
        file_id: fileId,
        path_pair_id: pathPairId,
        path_pair_name: pathPairName,
        name: "dup"
    }, extraProps));
}


function getViewFilesById(viewFiles: Immutable.List<ViewFile>): Map<string, ViewFile> {
    const filesById = new Map<string, ViewFile>();
    viewFiles.forEach(file => filesById.set(file.fileId, file));
    return filesById;
}


function createModelFiles(count: number): Immutable.Map<string, ModelFile> {
    let modelFiles = Immutable.Map<string, ModelFile>();
    for (let index = 0; index < count; index++) {
        const fileId = `file-${index}`;
        modelFiles = modelFiles.set(fileId, new ModelFile({
            file_id: fileId,
            name: fileId
        }));
    }

    return modelFiles;
}


function createViewService(): ViewFileService {
    return new ViewFileService(
        TestBed.get(LoggerService),
        TestBed.get(StreamServiceRegistry),
        TestBed.get(FileSelectionService),
        TestBed.get(LOCAL_STORAGE)
    );
}


describe("Testing view file service", () => {
    let viewService: ViewFileService;
    let mockModelService: MockModelFileService;
    let storageService: StorageService;

    beforeEach(() => {
        TestBed.configureTestingModule({
            providers: [
                ViewFileService,
                FileSelectionService,
                LoggerService,
                ConnectedService,
                {provide: StreamServiceRegistry, useClass: MockStreamServiceRegistry},
                {provide: LOCAL_STORAGE, useClass: MockStorageService}
            ]
        });

        viewService = createViewService();
        let mockRegistry: MockStreamServiceRegistry = TestBed.get(StreamServiceRegistry);
        mockModelService = mockRegistry.modelFileService;
        storageService = TestBed.get(LOCAL_STORAGE);
    });

    it("should create an instance", () => {
        expect(viewService).toBeDefined();
    });

    it("should forward an empty model by default", fakeAsync(() => {
        let count = 0;

        viewService.files.subscribe({
            next: list => {
                expect(list.size).toBe(0);
                count++;
            }
        });

        tick();
        expect(count).toBe(1);
    }));

    it("should forward an empty model", fakeAsync(() => {
        let model = Immutable.Map<string, ModelFile>();
        mockModelService._files.next(model);
        tick();

        let count = 0;
        viewService.files.subscribe({
            next: list => {
                expect(list.size).toBe(0);
                count++;
            }
        });
        tick();
        expect(count).toBe(1);
    }));

    it("should correctly populate ViewFile props from a ModelFile", fakeAsync(() => {
        let model = Immutable.Map<string, ModelFile>();
        model = model.set("a", new ModelFile({
            file_id: "[\"movies\",\"a\"]",
            path_pair_id: "movies",
            path_pair_name: "Movies",
            name: "a",
            is_dir: true,
            local_size: 0,
            remote_size: 11,
            state: ModelFile.State.DEFAULT,
            downloading_speed: 111,
            eta: 1111,
            full_path: "root/a",
            is_extractable: true,
            local_created_timestamp: new Date("November 9, 2018 21:40:18"),
            local_modified_timestamp: new Date(1541828418943),
            remote_created_timestamp: null,
            remote_modified_timestamp: new Date(1541828418943),
        }));
        mockModelService._files.next(model);
        tick();

        let count = 0;
        viewService.files.subscribe({
            next: list => {
                expect(list.size).toBe(1);
                let file = list.get(0);
                expect(file.fileId).toBe("[\"movies\",\"a\"]");
                expect(file.pathPairId).toBe("movies");
                expect(file.pathPairName).toBe("Movies");
                expect(file.name).toBe("a");
                expect(file.isDir).toBe(true);
                expect(file.localSize).toBe(0);
                expect(file.remoteSize).toBe(11);
                expect(file.transferredSize).toBe(0);
                expect(file.status).toBe(ViewFile.Status.DEFAULT);
                expect(file.downloadingSpeed).toBe(111);
                expect(file.eta).toBe(1111);
                expect(file.fullPath).toBe("root/a");
                expect(file.isArchive).toBe(true);
                expect(file.localCreatedTimestamp).toEqual(new Date("November 9, 2018 21:40:18"));
                expect(file.localModifiedTimestamp).toEqual(new Date(1541828418943));
                expect(file.remoteCreatedTimestamp).toBeNull();
                expect(file.remoteModifiedTimestamp).toEqual(new Date(1541828418943));
                count++;
            }
        });
        tick();
        expect(count).toBe(1);
    }));

    it("should expose only retry move and reliable local delete for move failed", fakeAsync(() => {
        mockModelService._files.next(Immutable.Map<string, ModelFile>().set("movie", new ModelFile({
            file_id: "movie",
            name: "movie",
            state: ModelFile.State.MOVE_FAILED,
            local_size: 100,
            remote_size: 100,
            is_stoppable: true,
            is_extractable: true
        })));
        tick();

        let file: ViewFile = null;
        viewService.files.subscribe(files => file = files.get(0));
        tick();

        expect(file.status).toBe(ViewFile.Status.MOVE_FAILED);
        expect(file.isMoveRetryable).toBe(true);
        expect(file.isLocallyDeletable).toBe(true);
        expect(file.isQueueable).toBe(false);
        expect(file.isStoppable).toBe(false);
        expect(file.isExtractable).toBe(false);
        expect(file.isRemotelyDeletable).toBe(false);
        expect(file.isValidatable).toBe(false);
    }));

    it("should refine only ordinary downloaded state to final move succeeded", fakeAsync(() => {
        mockModelService._files.next(Immutable.Map<string, ModelFile>()
            .set("downloaded", new ModelFile({
                file_id: "downloaded", name: "downloaded", state: ModelFile.State.DOWNLOADED,
                final_move_succeeded: true, local_size: 100, remote_size: 100
            }))
            .set("validated", new ModelFile({
                file_id: "validated", name: "validated", state: ModelFile.State.VALIDATED,
                final_move_succeeded: true, local_size: 100, remote_size: 100
            })));
        tick();

        let files: Immutable.List<ViewFile> = null;
        viewService.files.subscribe(value => files = value);
        tick();

        expect(files.find(file => file.fileId === "downloaded").status)
            .toBe(ViewFile.Status.MOVE_SUCCEEDED);
        expect(files.find(file => file.fileId === "validated").status)
            .toBe(ViewFile.Status.VALIDATED);
        expect(files.find(file => file.fileId === "downloaded").isMoveRetryable).toBe(false);
    }));

    it("should prefer transferred size for active downloads", fakeAsync(() => {
        const testVectors = [
            {
                name: "idle",
                state: ModelFile.State.DEFAULT,
                local_size: 24,
                remote_size: 100,
                transferred_size: null,
                expected: 24
            },
            {
                name: "active",
                state: ModelFile.State.DOWNLOADING,
                local_size: 24,
                remote_size: 100,
                transferred_size: 18,
                expected: 18
            },
            {
                name: "active-fallback",
                state: ModelFile.State.DOWNLOADING,
                local_size: 24,
                remote_size: 100,
                transferred_size: null,
                expected: 24
            }
        ];

        let count = -1;
        viewService.files.subscribe({
            next: list => {
                if (count >= 0) {
                    expect(list.size).toBe(1);
                    const file = list.get(0);
                    expect(file.transferredSize).toBe(testVectors[count].expected);
                }
                count++;
            }
        });
        tick();
        expect(count).toBe(0);

        for (const vector of testVectors) {
            const model = Immutable.Map<string, ModelFile>().set("a", new ModelFile({
                name: vector.name,
                state: vector.state,
                local_size: vector.local_size,
                remote_size: vector.remote_size,
                transferred_size: vector.transferred_size
            }));
            mockModelService._files.next(model);
            tick();
        }
        expect(count).toBe(testVectors.length);
    }));

    it("should correctly set the ViewFile status", fakeAsync(() => {
        let modelFile = new ModelFile({
            name: "a",
            state: ModelFile.State.DEFAULT,
        });
        let model = Immutable.Map<string, ModelFile>();
        model = model.set(modelFile.name, modelFile);

        let expectedStates = [
            ViewFile.Status.DEFAULT,
            ViewFile.Status.QUEUED,
            ViewFile.Status.DOWNLOADING,
            ViewFile.Status.DOWNLOADED,
            ViewFile.Status.STOPPED,
            ViewFile.Status.DELETED,
            ViewFile.Status.EXTRACTING,
            ViewFile.Status.EXTRACTED
        ];

        // First state - DEFAULT
        mockModelService._files.next(model);
        tick();

        let count = 0;
        viewService.files.subscribe({
            next: list => {
                expect(list.size).toBe(1);
                let file = list.get(0);
                expect(file.status).toBe(expectedStates[count++]);
            }
        });
        tick();
        expect(count).toBe(1);

        // Next state - QUEUED
        modelFile = new ModelFile(modelFile.set("state", ModelFile.State.QUEUED));
        model = model.set(modelFile.name, modelFile);
        mockModelService._files.next(model);
        tick();
        expect(count).toBe(2);

        // Next state - DOWNLOADING
        modelFile = new ModelFile(modelFile.set("state", ModelFile.State.DOWNLOADING));
        model = model.set(modelFile.name, modelFile);
        mockModelService._files.next(model);
        tick();
        expect(count).toBe(3);

        // Next state - DOWNLOADED
        modelFile = new ModelFile(modelFile.set("state", ModelFile.State.DOWNLOADED));
        model = model.set(modelFile.name, modelFile);
        mockModelService._files.next(model);
        tick();
        expect(count).toBe(4);

        // Next state - STOPPED
        // local size and remote size > 0
        modelFile = new ModelFile(modelFile.set("state", ModelFile.State.DEFAULT));
        modelFile = new ModelFile(modelFile.set("local_size", 50));
        modelFile = new ModelFile(modelFile.set("remote_size", 50));
        modelFile = new ModelFile(modelFile.set("remote_present", true));
        modelFile = new ModelFile(modelFile.set("local_present", true));
        modelFile = new ModelFile(modelFile.set("remote_has_transferable_content", true));
        model = model.set(modelFile.name, modelFile);
        mockModelService._files.next(model);
        tick();
        expect(count).toBe(5);

        // Next state - DELETED
        modelFile = new ModelFile(modelFile.set("state", ModelFile.State.DELETED));
        model = model.set(modelFile.name, modelFile);
        mockModelService._files.next(model);
        tick();
        expect(count).toBe(6);

        // Next state - DELETED
        modelFile = new ModelFile(modelFile.set("state", ModelFile.State.EXTRACTING));
        model = model.set(modelFile.name, modelFile);
        mockModelService._files.next(model);
        tick();
        expect(count).toBe(7);

        // Next state - DELETED
        modelFile = new ModelFile(modelFile.set("state", ModelFile.State.EXTRACTED));
        model = model.set(modelFile.name, modelFile);
        mockModelService._files.next(model);
        tick();
        expect(count).toBe(8);
    }));

    it("should always set a non-null file sizes in ViewFile", fakeAsync(() => {
        let model = Immutable.Map<string, ModelFile>();
        model = model.set("a", new ModelFile({
            name: "a",
            local_size: null,
            remote_size: null,
        }));
        mockModelService._files.next(model);
        tick();

        let count = 0;
        viewService.files.subscribe({
            next: list => {
                expect(list.size).toBe(1);
                let file = list.get(0);
                expect(file.localSize).toBe(0);
                expect(file.remoteSize).toBe(0);
                count++;
            }
        });
        tick();
        expect(count).toBe(1);
    }));

    it("should correctly set ViewFile percent downloaded", fakeAsync(() => {
        // Test vectors of local size, remote size, state, transfer progress, percentage
        let testVectors = [
            [24, 100, ModelFile.State.DEFAULT, 60, 60],
            [24, 100, ModelFile.State.DOWNLOADING, 60, 60],
            [24, 100, ModelFile.State.DOWNLOADING, null, 24],
            [null, 100, ModelFile.State.DOWNLOADING, 60, 60],
            [0, 0, ModelFile.State.DEFAULT, 60, 0]
        ];

        let count = -1;
        viewService.files.subscribe({
            next: list => {
                // Ignore first
                if(count >= 0) {
                    expect(list.size).toBe(1);
                    let file = list.get(0);
                    expect(file.percentDownloaded).toBe(testVectors[count][4]);
                }
                count++;
            }
        });
        tick();
        expect(count).toBe(0);

        // Send over the test vectors
        for(let vector of testVectors) {
            let model = Immutable.Map<string, ModelFile>();
            model = model.set("a", new ModelFile({
                name: "a",
                local_size: vector[0],
                remote_size: vector[1],
                state: vector[2],
                download_progress: vector[3],
            }));
            mockModelService._files.next(model);
            tick();
        }
        expect(count).toBe(testVectors.length);
    }));

    it("should preserve retained stopped progress from transferred size and snapshot percent", fakeAsync(() => {
        const model = Immutable.Map<string, ModelFile>().set("partial", new ModelFile({
            name: "partial",
            state: ModelFile.State.DEFAULT,
            local_size: 50,
            remote_size: 100,
            transferred_size: 75,
            download_progress: 75
        }));
        mockModelService._files.next(model);
        tick();

        let count = 0;
        viewService.files.subscribe({
            next: list => {
                expect(list.size).toBe(1);
                const file = list.get(0);
                expect(file.status).toBe(ViewFile.Status.STOPPED);
                expect(file.transferredSize).toBe(75);
                expect(file.percentDownloaded).toBe(75);
                count++;
            }
        });
        tick();
        expect(count).toBe(1);
    }));

    it("should mark retained-progress rows as stopped even when local size is zero", fakeAsync(() => {
        const model = Immutable.Map<string, ModelFile>().set("partial", new ModelFile({
            name: "partial",
            state: ModelFile.State.DEFAULT,
            local_size: 0,
            remote_size: 100,
            transferred_size: 25,
            download_progress: 25
        }));
        mockModelService._files.next(model);
        tick();

        let latestFile: ViewFile = null;
        viewService.files.subscribe({
            next: list => {
                latestFile = list.get(0);
            }
        });
        tick();

        expect(latestFile.status).toBe(ViewFile.Status.STOPPED);
        expect(latestFile.transferredSize).toBe(25);
        expect(latestFile.percentDownloaded).toBe(25);
    }));

    it("should render explicit local presence as Local Only when remote content is missing", fakeAsync(() => {
        const model = Immutable.Map<string, ModelFile>().set("partial", new ModelFile({
            name: "partial",
            state: ModelFile.State.DEFAULT,
            local_size: 0,
            remote_size: null,
            transferred_size: 25,
            download_progress: 25,
            remote_present: false,
            local_present: true,
            remote_has_transferable_content: false,
        }));
        mockModelService._files.next(model);
        tick();

        let latestFile: ViewFile = null;
        viewService.files.subscribe({
            next: list => {
                latestFile = list.get(0);
            }
        });
        tick();

        expect(latestFile.status).toBe(ViewFile.Status.DEFAULT);
        expect(latestFile.transferredSize).toBe(0);
        expect(latestFile.percentDownloaded).toBe(100);
        expect(latestFile.remoteSize).toBe(0);
    }));

    it("should treat local-only default files as complete", fakeAsync(() => {
        const model = Immutable.Map<string, ModelFile>().set("a", new ModelFile({
            name: "a",
            local_size: 24,
            remote_size: null,
            transferred_size: 5,
            state: ModelFile.State.DEFAULT
        }));
        mockModelService._files.next(model);
        tick();

        let count = 0;
        viewService.files.subscribe({
            next: list => {
                expect(list.size).toBe(1);
                const file = list.get(0);
                expect(file.status).toBe(ViewFile.Status.DEFAULT);
                expect(file.localSize).toBe(24);
                expect(file.remoteSize).toBe(0);
                expect(file.transferredSize).toBe(24);
                expect(file.displaySizeTotal).toBe(24);
                expect(file.percentDownloaded).toBe(100);
                count++;
            }
        });
        tick();
        expect(count).toBe(1);
    }));

    it("should treat local-only downloaded files as complete", fakeAsync(() => {
        const model = Immutable.Map<string, ModelFile>().set("a", new ModelFile({
            name: "a",
            local_size: 24,
            remote_size: null,
            transferred_size: 5,
            state: ModelFile.State.DOWNLOADED
        }));
        mockModelService._files.next(model);
        tick();

        let count = 0;
        viewService.files.subscribe({
            next: list => {
                expect(list.size).toBe(1);
                const file = list.get(0);
                expect(file.status).toBe(ViewFile.Status.DOWNLOADED);
                expect(file.localSize).toBe(24);
                expect(file.remoteSize).toBe(0);
                expect(file.transferredSize).toBe(24);
                expect(file.displaySizeTotal).toBe(24);
                expect(file.percentDownloaded).toBe(100);
                count++;
            }
        });
        tick();
        expect(count).toBe(1);
    }));

    it("should use explicit presence signals for zero-byte remote and local-only rows", fakeAsync(() => {
        const model = Immutable.Map<string, ModelFile>()
            .set("zero", new ModelFile({
                name: "zero",
                state: ModelFile.State.DEFAULT,
                local_size: null,
                remote_size: 0,
                remote_present: true,
                local_present: false,
                remote_has_transferable_content: true,
            }))
            .set("local", new ModelFile({
                name: "local",
                state: ModelFile.State.DEFAULT,
                local_size: 0,
                remote_size: null,
                remote_present: false,
                local_present: true,
                remote_has_transferable_content: false,
            }))
            // Empty remote directories remain manually deletable when they
            // have a local counterpart, but are never queueable as content.
            .set("empty-dir", new ModelFile({
                name: "empty-dir",
                is_dir: true,
                state: ModelFile.State.DEFAULT,
                local_size: 0,
                remote_size: 0,
                remote_present: true,
                local_present: true,
                remote_has_transferable_content: false,
            }));
        mockModelService._files.next(model);
        tick();

        let latestFiles: Immutable.List<ViewFile> = null;
        viewService.files.subscribe(files => latestFiles = files);
        tick();
        const remoteZero = latestFiles.find(file => file.name === "zero");
        const localOnly = latestFiles.find(file => file.name === "local");
        expect(remoteZero.remotePresent).toBe(true);
        expect(remoteZero.remoteHasTransferableContent).toBe(true);
        expect(remoteZero.isLocalOnly).toBe(false);
        expect(remoteZero.isQueueable).toBe(true);
        expect(localOnly.localPresent).toBe(true);
        expect(localOnly.isLocalOnly).toBe(true);
        expect(localOnly.percentDownloaded).toBe(100);
        expect(localOnly.displaySizeTotal).toBe(0);
        expect(localOnly.isLocallyDeletable).toBe(true);
        const emptyDir = latestFiles.find(file => file.name === "empty-dir");
        expect(emptyDir.isQueueable).toBe(false);
        expect(emptyDir.isRemotelyDeletable).toBe(true);
    }));

    it("should project explicit signals across an incremental model update", fakeAsync(() => {
        const initial = new ModelFile({
            name: "changing",
            state: ModelFile.State.DEFAULT,
            remote_size: null,
            local_size: null,
            remote_present: false,
            local_present: false,
            remote_has_transferable_content: false,
        });
        mockModelService._files.next(Immutable.Map<string, ModelFile>().set("changing", initial));
        tick();
        const updated = new ModelFile({
            name: "changing",
            state: ModelFile.State.DEFAULT,
            remote_size: 0,
            local_size: null,
            remote_present: true,
            local_present: false,
            remote_has_transferable_content: true,
        });
        mockModelService._files.next(Immutable.Map<string, ModelFile>().set("changing", updated));
        tick();

        let latestFile: ViewFile = null;
        viewService.files.subscribe(files => latestFile = files.get(0));
        tick();

        const file = latestFile;
        expect(file.remotePresent).toBe(true);
        expect(file.remoteHasTransferableContent).toBe(true);
        expect(file.isLocalOnly).toBe(false);
        expect(file.isQueueable).toBe(true);
    }));

    it("should should correctly set ViewFile isQueueable", fakeAsync(() => {
        // Test and expected result vectors
        // test - [ModelFile.State, local size, remote size]
        // result - [isQueueable, ViewFile.Status]
        let testVectors: any[][][] = [
            // Default remote file is queueable
            [[ModelFile.State.DEFAULT, null, 100], [true, ViewFile.Status.DEFAULT]],
            // Default local file is NOT queueable
            [[ModelFile.State.DEFAULT, 100, null], [false, ViewFile.Status.DEFAULT]],
            // Stopped file is queueable
            [[ModelFile.State.DEFAULT, 50, 100], [true, ViewFile.Status.STOPPED]],
            // Deleted file is queueable
            [[ModelFile.State.DELETED, null, 100], [true, ViewFile.Status.DELETED]],
            // Queued file is NOT queueable
            [[ModelFile.State.QUEUED, null, 100], [false, ViewFile.Status.QUEUED]],
            // Downloading file is NOT queueable
            [[ModelFile.State.DOWNLOADING, 10, 100], [false, ViewFile.Status.DOWNLOADING]],
            // Downloaded file is NOT queueable
            [[ModelFile.State.DOWNLOADED, 100, 100], [false, ViewFile.Status.DOWNLOADED]],
            // Extracting file is NOT queueable
            [[ModelFile.State.EXTRACTING, 100, 100], [false, ViewFile.Status.EXTRACTING]],
            // Extracting local-only file is NOT queueable
            [[ModelFile.State.EXTRACTING, 100, null], [false, ViewFile.Status.EXTRACTING]],
            // Extracted file is NOT queueable
            [[ModelFile.State.EXTRACTED, 100, 100], [false, ViewFile.Status.EXTRACTED]],
        ];

        let count = -1;
        viewService.files.subscribe({
            next: list => {
                // Ignore first
                if(count >= 0) {
                    expect(list.size).toBe(1);
                    let file = list.get(0);
                    let resultVector = testVectors[count][1];
                    expect(file.isQueueable).toBe(resultVector[0]);
                    expect(file.status).toBe(resultVector[1]);
                }
                count++;
            }
        });
        tick();
        expect(count).toBe(0);

        // Send over the test vectors
        for(let vector of testVectors) {
            let testVector = vector[0];
            let model = Immutable.Map<string, ModelFile>();
            model = model.set("a", new ModelFile({
                name: "a",
                state: testVector[0],
                local_size: testVector[1],
                remote_size: testVector[2],
            }));
            mockModelService._files.next(model);
            tick();
        }
        expect(count).toBe(testVectors.length);
    }));

    it("should should correctly set ViewFile isStoppable", fakeAsync(() => {
        // Test and expected result vectors
        // test - [ModelFile.State, local size, remote size, is_stoppable]
        // result - [isStoppable, ViewFile.Status]
        let testVectors: any[][][] = [
            // Default remote file is NOT stoppable
            [[ModelFile.State.DEFAULT, null, 100, false], [false, ViewFile.Status.DEFAULT]],
            // Default local file is NOT stoppable
            [[ModelFile.State.DEFAULT, 100, null, false], [false, ViewFile.Status.DEFAULT]],
            // Stopped file is NOT stoppable
            [[ModelFile.State.DEFAULT, 50, 100, false], [false, ViewFile.Status.STOPPED]],
            // Deleted file is NOT stoppable
            [[ModelFile.State.DELETED, null, 100, false], [false, ViewFile.Status.DELETED]],
            // Queued file is stoppable
            [[ModelFile.State.QUEUED, null, 100, true], [true, ViewFile.Status.QUEUED]],
            // Downloading file is stoppable once resumable metadata exists
            [[ModelFile.State.DOWNLOADING, 10, 100, true], [true, ViewFile.Status.DOWNLOADING]],
            // Downloading file without resumable metadata is not stoppable
            [[ModelFile.State.DOWNLOADING, 0, 100, false], [false, ViewFile.Status.DOWNLOADING]],
            // Downloaded file is NOT stoppable
            [[ModelFile.State.DOWNLOADED, 100, 100, false], [false, ViewFile.Status.DOWNLOADED]],
            // Extracting file is NOT stoppable
            [[ModelFile.State.EXTRACTING, 100, 100, false], [false, ViewFile.Status.EXTRACTING]],
            // Extracted file is NOT stoppable
            [[ModelFile.State.EXTRACTED, 100, 100, false], [false, ViewFile.Status.EXTRACTED]],
        ];

        let count = -1;
        viewService.files.subscribe({
            next: list => {
                // Ignore first
                if(count >= 0) {
                    expect(list.size).toBe(1);
                    let file = list.get(0);
                    let resultVector = testVectors[count][1];
                    expect(file.isStoppable).toBe(resultVector[0]);
                    expect(file.status).toBe(resultVector[1]);
                }
                count++;
            }
        });
        tick();
        expect(count).toBe(0);

        // Send over the test vectors
        for(let vector of testVectors) {
            let testVector = vector[0];
            let model = Immutable.Map<string, ModelFile>();
            model = model.set("a", new ModelFile({
                name: "a",
                state: testVector[0],
                local_size: testVector[1],
                remote_size: testVector[2],
                is_stoppable: testVector[3],
            }));
            mockModelService._files.next(model);
            tick();
        }
        expect(count).toBe(testVectors.length);
    }));

    it("should should correctly set ViewFile isExtractable", fakeAsync(() => {
        // Test and expected result vectors
        // test - [ModelFile.State, local size, remote size]
        // result - [isExtractable, ViewFile.Status]
        let testVectors: any[][][] = [
            // Default remote file is NOT extractable
            [[ModelFile.State.DEFAULT, null, 100], [false, ViewFile.Status.DEFAULT]],
            // Default local file is extractable
            [[ModelFile.State.DEFAULT, 100, null], [true, ViewFile.Status.DEFAULT]],
            // Stopped file is extractable
            [[ModelFile.State.DEFAULT, 50, 100], [true, ViewFile.Status.STOPPED]],
            // Deleted file is NOT extractable
            [[ModelFile.State.DELETED, null, 100], [false, ViewFile.Status.DELETED]],
            // Queued file is NOT extractable
            [[ModelFile.State.QUEUED, null, 100], [false, ViewFile.Status.QUEUED]],
            // Downloading file is NOT extractable
            [[ModelFile.State.DOWNLOADING, 10, 100], [false, ViewFile.Status.DOWNLOADING]],
            // Downloaded file is extractable
            [[ModelFile.State.DOWNLOADED, 100, 100], [true, ViewFile.Status.DOWNLOADED]],
            // Extracting file is NOT extractable
            [[ModelFile.State.EXTRACTING, 100, 100], [false, ViewFile.Status.EXTRACTING]],
            // Extracted file is extractable
            [[ModelFile.State.EXTRACTED, 100, 100], [true, ViewFile.Status.EXTRACTED]],
        ];

        let count = -1;
        viewService.files.subscribe({
            next: list => {
                // Ignore first
                if(count >= 0) {
                    expect(list.size).toBe(1);
                    let file = list.get(0);
                    let resultVector = testVectors[count][1];
                    expect(file.isExtractable).toBe(resultVector[0]);
                    expect(file.status).toBe(resultVector[1]);
                }
                count++;
            }
        });
        tick();
        expect(count).toBe(0);

        // Send over the test vectors
        for(let vector of testVectors) {
            let testVector = vector[0];
            let model = Immutable.Map<string, ModelFile>();
            model = model.set("a", new ModelFile({
                name: "a",
                state: testVector[0],
                local_size: testVector[1],
                remote_size: testVector[2],
            }));
            mockModelService._files.next(model);
            tick();
        }
        expect(count).toBe(testVectors.length);
    }));

    it("should map validation states and flags into the view model", fakeAsync(() => {
        const testVectors: any[][][] = [
            [[ModelFile.State.VALIDATING, 100, 100], [ViewFile.Status.VALIDATING, false]],
            [[ModelFile.State.VALIDATED, 100, 100], [ViewFile.Status.VALIDATED, true]],
            [[ModelFile.State.CORRUPT, 100, 100], [ViewFile.Status.CORRUPT, true]],
            [[ModelFile.State.VALIDATED, null, null], [ViewFile.Status.VALIDATED, false]]
        ];

        let count = -1;
        viewService.files.subscribe({
            next: list => {
                if (count >= 0) {
                    const file = list.get(0);
                    expect(file.status).toBe(testVectors[count][1][0]);
                    expect(file.isValidatable).toBe(testVectors[count][1][1]);
                }
                count++;
            }
        });
        tick();

        for (const vector of testVectors) {
            const model = Immutable.Map<string, ModelFile>().set("a", new ModelFile({
                name: "a",
                state: vector[0][0],
                local_size: vector[0][1],
                remote_size: vector[0][2],
                validation_progress: 0.5,
                validation_error: "checksum mismatch",
                corrupt_chunks: [1]
            }));
            mockModelService._files.next(model);
            tick();
        }

        expect(count).toBe(testVectors.length);
    }));

    it("should not mark stopped partial files as validatable", fakeAsync(() => {
        let latestFile: ViewFile = null;
        viewService.files.subscribe({
            next: list => {
                latestFile = list.get(0);
            }
        });
        tick();

        const model = Immutable.Map<string, ModelFile>().set("partial", new ModelFile({
            name: "partial",
            state: ModelFile.State.DEFAULT,
            local_size: 50,
            remote_size: 100
        }));
        mockModelService._files.next(model);
        tick();

        expect(latestFile.status).toBe(ViewFile.Status.STOPPED);
        expect(latestFile.isValidatable).toBe(false);
    }));

    // it("should sort view files by status then name", fakeAsync(() => {
    //     // Test vectors to create model file
    //     // name, ModelFile.State, local size, remote size
    //     let testVector: any[][] = [
    //         ["a", ModelFile.State.DEFAULT, null, 100],
    //         ["b", ModelFile.State.DEFAULT, 100, null],
    //         ["c", ModelFile.State.DEFAULT, 50, 100],
    //         ["d", ModelFile.State.DELETED, null, 100],
    //         ["e", ModelFile.State.QUEUED, null, 100],
    //         ["f", ModelFile.State.DOWNLOADING, 50, 100],
    //         ["g", ModelFile.State.DOWNLOADED, 50, 100],
    //         ["h", ModelFile.State.EXTRACTING, 50, 100],
    //         ["i", ModelFile.State.EXTRACTED, 50, 100]
    //     ];
    //
    //     // Except result vector in order of view file name and state
    //     let resultVector: any[][] = [
    //         ["h", ViewFile.Status.EXTRACTING],
    //         ["f", ViewFile.Status.DOWNLOADING],
    //         ["e", ViewFile.Status.QUEUED],
    //         ["i", ViewFile.Status.EXTRACTED],
    //         ["g", ViewFile.Status.DOWNLOADED],
    //         ["c", ViewFile.Status.STOPPED],
    //         ["a", ViewFile.Status.DEFAULT],
    //         ["b", ViewFile.Status.DEFAULT],
    //         ["d", ViewFile.Status.DELETED]
    //     ];
    //
    //     let model = Immutable.Map<string, ModelFile>();
    //     for(let vector of testVector) {
    //         model = model.set(vector[0], new ModelFile({
    //             name: vector[0],
    //             state: vector[1],
    //             local_size: vector[2],
    //             remote_size: vector[3],
    //         }));
    //     }
    //     mockModelService._files.next(model);
    //     tick();
    //
    //     let count = 0;
    //     viewService.files.subscribe({
    //         next: list => {
    //             expect(list.size).toBe(resultVector.length);
    //             resultVector.forEach((item, index) => {
    //                 let file = list.get(index);
    //                 expect(file.name).toBe(item[0]);
    //                 expect(file.status).toBe(item[1]);
    //             });
    //             count++;
    //         }
    //     });
    //     tick();
    //     expect(count).toBe(1);
    // }));

    it("should correctly set and unset the selected file", fakeAsync(() => {
        let model = Immutable.Map<string, ModelFile>();
        model = model.set("a", new ModelFile({name: "a"}));
        model = model.set("b", new ModelFile({name: "b"}));
        model = model.set("c", new ModelFile({name: "c"}));

        let expectedSelectedFileIndex = -1;

        mockModelService._files.next(model);
        tick();

        let viewFileList;
        let count = 0;
        viewService.files.subscribe({
            next: list => {
                viewFileList = list;
                expect(list.size).toBe(3);
                expect(list.get(0).name).toBe("a");
                expect(list.get(1).name).toBe("b");
                expect(list.get(2).name).toBe("c");
                list.forEach((item, index) => {
                    // Only 1 file is selected at a time
                    if(index == expectedSelectedFileIndex) {
                        expect(item.isSelected).toBe(true);
                    } else {
                        expect(item.isSelected).toBe(false);
                    }
                });
                count++;
            }
        });

        tick();
        expect(count).toBe(1);

        // select "a"
        expectedSelectedFileIndex = 0;
        viewService.setSelected(viewFileList.get(0));
        tick();
        expect(count).toBe(2);

        // unselect "a"
        expectedSelectedFileIndex = -1;
        viewService.unsetSelected();
        tick();
        expect(count).toBe(3);

        // select "b"
        expectedSelectedFileIndex = 1;
        viewService.setSelected(viewFileList.get(1));
        tick();
        expect(count).toBe(4);

        // select "c"
        expectedSelectedFileIndex = 2;
        viewService.setSelected(viewFileList.get(2));
        tick();
        expect(count).toBe(5);

        // select "b" again
        expectedSelectedFileIndex = 1;
        viewService.setSelected(viewFileList.get(1));
        tick();
        expect(count).toBe(6);

        // unselect "b"
        expectedSelectedFileIndex = -1;
        viewService.unsetSelected();
        tick();
        expect(count).toBe(7);
    }));

    it("should keep duplicate display names distinct in the view model", fakeAsync(() => {
        const moviesId = "[\"movies\",\"dup\"]";
        const tvId = "[\"tv\",\"dup\"]";

        const model = Immutable.Map<string, ModelFile>()
            .set(moviesId, createDuplicateNamedModelFile(moviesId, "movies", "Movies"))
            .set(tvId, createDuplicateNamedModelFile(tvId, "tv", "TV"));

        mockModelService._files.next(model);
        tick();

        let viewFileList: Immutable.List<ViewFile> = null;
        viewService.files.subscribe({
            next: list => viewFileList = list
        });
        tick();

        const filesById = getViewFilesById(viewFileList);
        expect(filesById.size).toBe(2);
        expect(filesById.get(moviesId).name).toBe("dup");
        expect(filesById.get(moviesId).fileId).toBe(moviesId);
        expect(filesById.get(moviesId).pathPairId).toBe("movies");
        expect(filesById.get(moviesId).pathPairName).toBe("Movies");
        expect(filesById.get(tvId).name).toBe("dup");
        expect(filesById.get(tvId).fileId).toBe(tvId);
        expect(filesById.get(tvId).pathPairId).toBe("tv");
        expect(filesById.get(tvId).pathPairName).toBe("TV");
        expect(filesById.get(moviesId)).not.toBe(filesById.get(tvId));
    }));

    it("should select duplicate display names by file id and path pair", fakeAsync(() => {
        const moviesId = "[\"movies\",\"dup\"]";
        const tvId = "[\"tv\",\"dup\"]";

        const model = Immutable.Map<string, ModelFile>()
            .set(moviesId, createDuplicateNamedModelFile(moviesId, "movies", "Movies"))
            .set(tvId, createDuplicateNamedModelFile(tvId, "tv", "TV"));

        mockModelService._files.next(model);
        tick();

        let viewFileList: Immutable.List<ViewFile> = null;
        viewService.files.subscribe({
            next: list => viewFileList = list
        });
        tick();

        const filesById = getViewFilesById(viewFileList);
        viewService.setSelected(filesById.get(tvId));
        tick();

        let updatedFilesById = getViewFilesById(viewFileList);
        expect(updatedFilesById.get(moviesId).isSelected).toBe(false);
        expect(updatedFilesById.get(tvId).isSelected).toBe(true);
        expect(updatedFilesById.get(tvId).fileId).toBe(tvId);
        expect(updatedFilesById.get(tvId).pathPairId).toBe("tv");

        viewService.setSelected(updatedFilesById.get(moviesId));
        tick();

        updatedFilesById = getViewFilesById(viewFileList);
        expect(updatedFilesById.get(moviesId).isSelected).toBe(true);
        expect(updatedFilesById.get(moviesId).fileId).toBe(moviesId);
        expect(updatedFilesById.get(moviesId).pathPairId).toBe("movies");
        expect(updatedFilesById.get(tvId).isSelected).toBe(false);
    }));

    it("should delegate duplicate-name actions to the matching file id", fakeAsync(() => {
        const moviesId = "[\"movies\",\"dup\"]";
        const tvId = "[\"tv\",\"dup\"]";

        const model = Immutable.Map<string, ModelFile>()
            .set(moviesId, createDuplicateNamedModelFile(moviesId, "movies", "Movies"))
            .set(tvId, createDuplicateNamedModelFile(tvId, "tv", "TV"));

        mockModelService._files.next(model);
        tick();

        let viewFileList: Immutable.List<ViewFile> = null;
        viewService.files.subscribe({
            next: list => viewFileList = list
        });
        tick();

        const filesById = getViewFilesById(viewFileList);
        const queueReaction = new WebReaction(true, tvId, null);
        const queueSpy = jasmine.createSpy("queue").and.callFake((file: ModelFile) => {
            return of(queueReaction);
        });
        (mockModelService as any).queue = queueSpy;

        let latestReaction: WebReaction = null;
        viewService.queue(filesById.get(tvId)).subscribe({
            next: reaction => latestReaction = reaction
        });
        tick();

        expect(queueSpy).toHaveBeenCalledTimes(1);
        expect(queueSpy.calls.mostRecent().args[0].file_id).toBe(tvId);
        expect(queueSpy.calls.mostRecent().args[0].path_pair_id).toBe("tv");
        expect(latestReaction.success).toBe(true);
        expect(latestReaction.data).toBe(tvId);
    }));

    it("should cancel an in-flight action when unsubscribed", fakeAsync(() => {
        const tvId = "[\"tv\",\"dup\"]";
        const model = Immutable.Map<string, ModelFile>()
            .set(tvId, createDuplicateNamedModelFile(tvId, "tv", "TV"));

        mockModelService._files.next(model);
        tick();

        const queueReaction = new WebReaction(true, tvId, null);
        let cancelled = false;
        const queueSpy = jasmine.createSpy("queue").and.callFake(() => {
            return new Observable<WebReaction>(observer => {
                const timeoutId = setTimeout(() => {
                    observer.next(queueReaction);
                    observer.complete();
                }, 25);
                return () => {
                    cancelled = true;
                    clearTimeout(timeoutId);
                };
            });
        });
        (mockModelService as any).queue = queueSpy;

        let latestReaction: WebReaction = null;
        const subscription = viewService.queue(new ViewFile({fileId: tvId, name: "dup"})).subscribe({
            next: reaction => latestReaction = reaction
        });
        subscription.unsubscribe();
        tick(25);

        expect(queueSpy).toHaveBeenCalledTimes(1);
        expect(cancelled).toBe(true);
        expect(latestReaction).toBeNull();
    }));

    it("should log a generic not-found message for missing actions", fakeAsync(() => {
        const missingFileId = "[\"tv\",\"missing\"]";
        const errorSpy = spyOn(console, "error");

        let latestReaction: WebReaction = null;
        viewService.queue(new ViewFile({fileId: missingFileId, name: "missing"})).subscribe({
            next: reaction => latestReaction = reaction
        });
        tick();

        expect(errorSpy).toHaveBeenCalledWith("File not found: " + missingFileId);
        expect(latestReaction.success).toBe(false);
        expect(latestReaction.errorMessage).toBe("File 'missing' not found");
    }));

    it("should preserve duplicate-name identity across update and remove diffs", fakeAsync(() => {
        const moviesId = "[\"movies\",\"dup\"]";
        const tvId = "[\"tv\",\"dup\"]";

        let model = Immutable.Map<string, ModelFile>()
            .set(moviesId, createDuplicateNamedModelFile(moviesId, "movies", "Movies", {
                local_size: 10,
                remote_size: 20
            }))
            .set(tvId, createDuplicateNamedModelFile(tvId, "tv", "TV", {
                local_size: 30,
                remote_size: 40
            }));

        mockModelService._files.next(model);
        tick();

        let viewFileList: Immutable.List<ViewFile> = null;
        viewService.files.subscribe({
            next: list => viewFileList = list
        });
        tick();

        let filesById = getViewFilesById(viewFileList);
        viewService.setSelected(filesById.get(tvId));
        tick();

        model = model.set(moviesId, createDuplicateNamedModelFile(moviesId, "movies", "Movies", {
            local_size: 11,
            remote_size: 21,
            state: ModelFile.State.DOWNLOADING,
            download_progress: 55,
            is_stoppable: true
        }));
        mockModelService._files.next(model);
        tick();

        filesById = getViewFilesById(viewFileList);
        expect(filesById.size).toBe(2);
        expect(filesById.get(moviesId).fileId).toBe(moviesId);
        expect(filesById.get(moviesId).pathPairId).toBe("movies");
        expect(filesById.get(moviesId).localSize).toBe(11);
        expect(filesById.get(moviesId).status).toBe(ViewFile.Status.DOWNLOADING);
        expect(filesById.get(moviesId).isSelected).toBe(false);
        expect(filesById.get(tvId).fileId).toBe(tvId);
        expect(filesById.get(tvId).pathPairId).toBe("tv");
        expect(filesById.get(tvId).isSelected).toBe(true);

        model = model.remove(moviesId);
        mockModelService._files.next(model);
        tick();

        filesById = getViewFilesById(viewFileList);
        expect(filesById.size).toBe(1);
        expect(filesById.has(moviesId)).toBe(false);
        expect(filesById.get(tvId).fileId).toBe(tvId);
        expect(filesById.get(tvId).pathPairId).toBe("tv");
        expect(filesById.get(tvId).pathPairName).toBe("TV");
        expect(filesById.get(tvId).isSelected).toBe(true);
    }));

    it("should remove interleaved files in one pass and refresh surviving indices", fakeAsync(() => {
        let model = Immutable.Map<string, ModelFile>()
            .set("a", new ModelFile({file_id: "a", name: "a"}))
            .set("b", new ModelFile({file_id: "b", name: "b"}))
            .set("c", new ModelFile({file_id: "c", name: "c"}))
            .set("d", new ModelFile({file_id: "d", name: "d"}))
            .set("e", new ModelFile({file_id: "e", name: "e"}));

        let viewFileList: Immutable.List<ViewFile> = null;

        viewService.files.subscribe({
            next: list => viewFileList = list
        });

        mockModelService._files.next(model);
        tick();

        let filesById = getViewFilesById(viewFileList);
        model = model.remove("b").remove("d");
        mockModelService._files.next(model);
        tick();

        filesById = getViewFilesById(viewFileList);
        expect(viewFileList.map(file => file.name).toArray()).toEqual(["a", "c", "e"]);
        expect(filesById.has("b")).toBe(false);
        expect(filesById.has("d")).toBe(false);

        viewService.setSelected(filesById.get("c"));
        tick();

        filesById = getViewFilesById(viewFileList);
        expect(viewFileList.map(file => file.name).toArray()).toEqual(["a", "c", "e"]);
        expect(viewFileList.map(file => file.isSelected).toArray()).toEqual([false, true, false]);
        expect(filesById.get("c").isSelected).toBe(true);
        expect(filesById.get("e").isSelected).toBe(false);
    }));

    it("should should correctly set ViewFile isLocallyDeletable", fakeAsync(() => {
        // Test and expected result vectors
        // test - [ModelFile.State, local size, remote size]
        // result - [isLocallyDeletable, ViewFile.Status]
        let testVectors: any[][][] = [
            // Default remote file is NOT locally deletable
            [[ModelFile.State.DEFAULT, null, 100], [false, ViewFile.Status.DEFAULT]],
            // Default local file is locally deletable
            [[ModelFile.State.DEFAULT, 100, null], [true, ViewFile.Status.DEFAULT]],
            // Stopped file is locally deletable
            [[ModelFile.State.DEFAULT, 50, 100], [true, ViewFile.Status.STOPPED]],
            // Deleted file is NOT locally deletable
            [[ModelFile.State.DELETED, null, 100], [false, ViewFile.Status.DELETED]],
            // Queued file is NOT locally deletable
            [[ModelFile.State.QUEUED, null, 100], [false, ViewFile.Status.QUEUED]],
            // Downloading file is NOT locally deletable
            [[ModelFile.State.DOWNLOADING, 10, 100], [false, ViewFile.Status.DOWNLOADING]],
            // Downloaded file is locally deletable
            [[ModelFile.State.DOWNLOADED, 100, 100], [true, ViewFile.Status.DOWNLOADED]],
            // Extracting file is NOT locally deletable
            [[ModelFile.State.EXTRACTING, 100, 100], [false, ViewFile.Status.EXTRACTING]],
            // Extracted file is locally deletable
            [[ModelFile.State.EXTRACTED, 100, 100], [true, ViewFile.Status.EXTRACTED]],
        ];

        let count = -1;
        viewService.files.subscribe({
            next: list => {
                // Ignore first
                if(count >= 0) {
                    expect(list.size).toBe(1);
                    let file = list.get(0);
                    let resultVector = testVectors[count][1];
                    expect(file.isLocallyDeletable).toBe(resultVector[0]);
                    expect(file.status).toBe(resultVector[1]);
                }
                count++;
            }
        });
        tick();
        expect(count).toBe(0);

        // Send over the test vectors
        for(let vector of testVectors) {
            let testVector = vector[0];
            let model = Immutable.Map<string, ModelFile>();
            model = model.set("a", new ModelFile({
                name: "a",
                state: testVector[0],
                local_size: testVector[1],
                remote_size: testVector[2],
            }));
            mockModelService._files.next(model);
            tick();
        }
        expect(count).toBe(testVectors.length);
    }));

    it("should should correctly set ViewFile isRemotelyDeletable", fakeAsync(() => {
        // Test and expected result vectors
        // test - [ModelFile.State, local size, remote size]
        // result - [isRemotelyDeletable, ViewFile.Status]
        let testVectors: any[][][] = [
            // Default remote file is remotely deletable
            [[ModelFile.State.DEFAULT, null, 100], [true, ViewFile.Status.DEFAULT]],
            // Default local file is NOT remotely deletable
            [[ModelFile.State.DEFAULT, 100, null], [false, ViewFile.Status.DEFAULT]],
            // Stopped file is remotely deletable
            [[ModelFile.State.DEFAULT, 50, 100], [true, ViewFile.Status.STOPPED]],
            // Deleted file is remotely deletable
            [[ModelFile.State.DELETED, null, 100], [true, ViewFile.Status.DELETED]],
            // Queued file is NOT remotely deletable
            [[ModelFile.State.QUEUED, null, 100], [false, ViewFile.Status.QUEUED]],
            // Downloading file is NOT remotely deletable
            [[ModelFile.State.DOWNLOADING, 10, 100], [false, ViewFile.Status.DOWNLOADING]],
            // Downloaded file is remotely deletable
            [[ModelFile.State.DOWNLOADED, 100, 100], [true, ViewFile.Status.DOWNLOADED]],
            // Extracting file is NOT remotely deletable
            [[ModelFile.State.EXTRACTING, 100, 100], [false, ViewFile.Status.EXTRACTING]],
            // Extracted file is remotely deletable
            [[ModelFile.State.EXTRACTED, 100, 100], [true, ViewFile.Status.EXTRACTED]],
        ];

        let count = -1;
        viewService.files.subscribe({
            next: list => {
                // Ignore first
                if(count >= 0) {
                    expect(list.size).toBe(1);
                    let file = list.get(0);
                    let resultVector = testVectors[count][1];
                    expect(file.isRemotelyDeletable).toBe(resultVector[0]);
                    expect(file.status).toBe(resultVector[1]);
                }
                count++;
            }
        });
        tick();
        expect(count).toBe(0);

        // Send over the test vectors
        for(let vector of testVectors) {
            let testVector = vector[0];
            let model = Immutable.Map<string, ModelFile>();
            model = model.set("a", new ModelFile({
                name: "a",
                state: testVector[0],
                local_size: testVector[1],
                remote_size: testVector[2],
            }));
            mockModelService._files.next(model);
            tick();
        }
        expect(count).toBe(testVectors.length);
    }));

    it("should not filter any files by default", fakeAsync(() => {
        const model = Immutable.Map({
            "aaaa": new ModelFile({name: "aaaa", state: ModelFile.State.DEFAULT}),
            "tofu": new ModelFile({name: "tofu", state: ModelFile.State.QUEUED}),
            "flower": new ModelFile({name: "flower", state: ModelFile.State.QUEUED}),
            "power": new ModelFile({name: "power", state: ModelFile.State.DOWNLOADING}),
            "max": new ModelFile({name: "max", state: ModelFile.State.DOWNLOADED}),
            "mrx": new ModelFile({name: "mrx", state: ModelFile.State.EXTRACTING}),
            "blueman": new ModelFile({name: "blueman", state: ModelFile.State.EXTRACTED}),
            "spicy": new ModelFile({name: "spicy", state: ModelFile.State.DELETED}),
        });
        mockModelService._files.next(model);

        let count = 0;
        let viewFiles: Immutable.List<ViewFile> = null;
        viewService.filteredFiles.subscribe({
            next: list => {
                viewFiles = list;
                count++;
            }
        });
        tick();
        expect(count).toBe(1);
        expect(viewFiles.size).toBe(8);
    }));

    it("should apply filter criteria correctly", fakeAsync(() => {
        class TestCriteria implements ViewFileFilterCriteria {
            meetsCriteria(viewFile: ViewFile): boolean {
                return viewFile.status === ViewFile.Status.QUEUED ||
                    viewFile.status === ViewFile.Status.EXTRACTED;
            }

        }
        viewService.setFilterCriteria(new TestCriteria());

        const model = Immutable.Map({
            "aaaa": new ModelFile({name: "aaaa", state: ModelFile.State.DEFAULT}),
            "tofu": new ModelFile({name: "tofu", state: ModelFile.State.QUEUED}),
            "flower": new ModelFile({name: "flower", state: ModelFile.State.QUEUED}),
            "power": new ModelFile({name: "power", state: ModelFile.State.DOWNLOADING}),
            "max": new ModelFile({name: "max", state: ModelFile.State.DOWNLOADED}),
            "mrx": new ModelFile({name: "mrx", state: ModelFile.State.EXTRACTING}),
            "blueman": new ModelFile({name: "blueman", state: ModelFile.State.EXTRACTED}),
            "spicy": new ModelFile({name: "spicy", state: ModelFile.State.DELETED}),
        });
        mockModelService._files.next(model);
        tick();

        let count = 0;
        let viewFiles: Immutable.List<ViewFile> = null;
        let viewFilesMap: Map<string, ViewFile> = null;
        viewService.filteredFiles.subscribe({
            next: list => {
                viewFiles = list;
                viewFilesMap = new Map<string, ViewFile>();
                list.forEach(value => viewFilesMap.set(value.name, value));
                count++;
            }
        });
        tick();
        expect(count).toBe(1);
        expect(viewFiles.size).toBe(3);
        expect(viewFilesMap.has("tofu")).toBe(true);
        expect(viewFilesMap.has("flower")).toBe(true);
        expect(viewFilesMap.has("blueman")).toBe(true);
    }));

    it("should clear bulk selection when filter criteria changes", fakeAsync(() => {
        class TestCriteria implements ViewFileFilterCriteria {
            meetsCriteria(viewFile: ViewFile): boolean {
                return viewFile.status === ViewFile.Status.QUEUED;
            }
        }

        const fileSelectionService = TestBed.get(FileSelectionService);
        const one = new ViewFile({name: "one"});
        const two = new ViewFile({name: "two"});

        fileSelectionService.setVisibleFiles(Immutable.List<ViewFile>([one, two]));
        fileSelectionService.toggle(one);
        fileSelectionService.toggle(two);

        let selectedNames = Immutable.Set<string>();
        fileSelectionService.selectedNames.subscribe(value => selectedNames = value);
        tick();
        expect(selectedNames.size).toBe(2);

        viewService.setFilterCriteria(new TestCriteria());
        tick();

        expect(selectedNames.size).toBe(0);
    }));

    it("should resend filtered files on criteria change", fakeAsync(() => {
        class TestCriteria implements ViewFileFilterCriteria {
            constructor(public flag: boolean) {}
            meetsCriteria(viewFile: ViewFile): boolean {
                if (this.flag) {
                    return viewFile.status === ViewFile.Status.QUEUED;
                } else {
                    return viewFile.status === ViewFile.Status.EXTRACTED;
                }
            }

        }
        viewService.setFilterCriteria(new TestCriteria(true));

        let count = 0;
        let viewFiles: Immutable.List<ViewFile> = null;
        let viewFilesMap: Map<string, ViewFile> = null;
        viewService.filteredFiles.subscribe({
            next: list => {
                viewFiles = list;
                viewFilesMap = new Map<string, ViewFile>();
                list.forEach(value => viewFilesMap.set(value.name, value));
                count++;
            }
        });
        expect(count).toBe(1);

        const model = Immutable.Map({
            "aaaa": new ModelFile({name: "aaaa", state: ModelFile.State.DEFAULT}),
            "tofu": new ModelFile({name: "tofu", state: ModelFile.State.QUEUED}),
            "flower": new ModelFile({name: "flower", state: ModelFile.State.QUEUED}),
            "power": new ModelFile({name: "power", state: ModelFile.State.DOWNLOADING}),
            "max": new ModelFile({name: "max", state: ModelFile.State.DOWNLOADED}),
            "mrx": new ModelFile({name: "mrx", state: ModelFile.State.EXTRACTING}),
            "blueman": new ModelFile({name: "blueman", state: ModelFile.State.EXTRACTED}),
            "spicy": new ModelFile({name: "spicy", state: ModelFile.State.DELETED}),
        });
        mockModelService._files.next(model);
        tick();

        expect(count).toBe(2);
        expect(viewFiles.size).toBe(2);
        expect(viewFilesMap.has("tofu")).toBe(true);
        expect(viewFilesMap.has("flower")).toBe(true);

        // Update the filter criteria
        viewService.setFilterCriteria(new TestCriteria(false));

        expect(count).toBe(3);
        expect(viewFiles.size).toBe(1);
        expect(viewFilesMap.has("blueman")).toBe(true);
    }));

    it("should suppress unchanged filtered lists but emit a real row update", fakeAsync(() => {
        class MatchAllCriteria implements ViewFileFilterCriteria {
            meetsCriteria(_viewFile: ViewFile): boolean {
                return true;
            }
        }

        viewService.setFilterCriteria(new MatchAllCriteria());
        let emissionCount = 0;
        let latestFile: ViewFile = null;
        viewService.filteredFiles.subscribe(files => {
            emissionCount++;
            latestFile = files.get(0) || null;
        });

        const initialFile = new ModelFile({name: "movie", state: ModelFile.State.DEFAULT});
        const initialModel = Immutable.Map<string, ModelFile>().set("movie", initialFile);
        mockModelService._files.next(initialModel);
        tick();
        expect(emissionCount).toBe(2);
        expect(latestFile.status).toBe(ViewFile.Status.DEFAULT);

        // Replaying the same row references does not require another filtered
        // list emission.
        mockModelService._files.next(initialModel);
        tick();
        expect(emissionCount).toBe(2);

        const updatedModel = initialModel.set("movie", new ModelFile(initialFile.set(
            "state", ModelFile.State.QUEUED
        )));
        mockModelService._files.next(updatedModel);
        tick();
        expect(emissionCount).toBe(3);
        expect(latestFile.status).toBe(ViewFile.Status.QUEUED);
    }));

    it("should coalesce rapid model emissions to the latest batch", fakeAsync(() => {
        const coalescedService = new ViewFileService(
            TestBed.get(LoggerService),
            TestBed.get(StreamServiceRegistry),
            TestBed.get(FileSelectionService),
            TestBed.get(LOCAL_STORAGE),
            100
        );
        let emissionCount = 0;
        let latestFile: ViewFile = null;
        coalescedService.files.subscribe(files => {
            emissionCount++;
            latestFile = files.get(0) || null;
        });

        const firstModel = Immutable.Map<string, ModelFile>().set("movie", new ModelFile({
            name: "movie", state: ModelFile.State.DEFAULT
        }));
        const latestModel = firstModel.set("movie", new ModelFile({
            name: "movie", state: ModelFile.State.QUEUED
        }));
        mockModelService._files.next(firstModel);
        mockModelService._files.next(latestModel);

        tick(99);
        expect(emissionCount).toBe(1);
        tick(1);
        expect(emissionCount).toBe(2);
        expect(latestFile.status).toBe(ViewFile.Status.QUEUED);
    }));

    it("should not sort files by default", fakeAsync(() => {
        const model = Immutable.Map({
            "aaaa": new ModelFile({name: "aaaa", state: ModelFile.State.DEFAULT}),
            "tofu": new ModelFile({name: "tofu", state: ModelFile.State.QUEUED}),
            "flower": new ModelFile({name: "flower", state: ModelFile.State.QUEUED}),
            "power": new ModelFile({name: "power", state: ModelFile.State.DOWNLOADING}),
            "max": new ModelFile({name: "max", state: ModelFile.State.DOWNLOADED}),
            "mrx": new ModelFile({name: "mrx", state: ModelFile.State.EXTRACTING}),
            "blueman": new ModelFile({name: "blueman", state: ModelFile.State.EXTRACTED}),
            "spicy": new ModelFile({name: "spicy", state: ModelFile.State.DELETED}),
        });
        mockModelService._files.next(model);

        let count = 0;
        let viewFiles: Immutable.List<ViewFile> = null;
        viewService.files.subscribe({
            next: list => {
                viewFiles = list;
                count++;
            }
        });
        tick();
        expect(count).toBe(1);
        expect(viewFiles.size).toBe(8);
        expect(viewFiles.get(0).name).toBe("aaaa");
        expect(viewFiles.get(1).name).toBe("tofu");
        expect(viewFiles.get(2).name).toBe("flower");
        expect(viewFiles.get(3).name).toBe("power");
        expect(viewFiles.get(4).name).toBe("max");
        expect(viewFiles.get(5).name).toBe("mrx");
        expect(viewFiles.get(6).name).toBe("blueman");
        expect(viewFiles.get(7).name).toBe("spicy");
    }));

    it("should sort new model correctly", fakeAsync(() => {
        const comparator: ViewFileComparator = function(a: ViewFile, b: ViewFile) {
            // alphabetical order
            return a.name.localeCompare(b.name);
        };
        viewService.setComparator(comparator);

        const model = Immutable.Map({
            "aaaa": new ModelFile({name: "aaaa", state: ModelFile.State.DEFAULT}),
            "tofu": new ModelFile({name: "tofu", state: ModelFile.State.QUEUED}),
            "flower": new ModelFile({name: "flower", state: ModelFile.State.QUEUED}),
            "power": new ModelFile({name: "power", state: ModelFile.State.DOWNLOADING}),
            "max": new ModelFile({name: "max", state: ModelFile.State.DOWNLOADED}),
            "mrx": new ModelFile({name: "mrx", state: ModelFile.State.EXTRACTING}),
            "blueman": new ModelFile({name: "blueman", state: ModelFile.State.EXTRACTED}),
            "spicy": new ModelFile({name: "spicy", state: ModelFile.State.DELETED}),
        });
        mockModelService._files.next(model);

        let count = 0;
        let viewFiles: Immutable.List<ViewFile> = null;
        viewService.files.subscribe({
            next: list => {
                viewFiles = list;
                count++;
            }
        });
        tick();
        expect(count).toBe(1);
        expect(viewFiles.size).toBe(8);
        expect(viewFiles.get(0).name).toBe("aaaa");
        expect(viewFiles.get(1).name).toBe("blueman");
        expect(viewFiles.get(2).name).toBe("flower");
        expect(viewFiles.get(3).name).toBe("max");
        expect(viewFiles.get(4).name).toBe("mrx");
        expect(viewFiles.get(5).name).toBe("power");
        expect(viewFiles.get(6).name).toBe("spicy");
        expect(viewFiles.get(7).name).toBe("tofu");
    }));

    it("should sort existing model on setComparator", fakeAsync(() => {
        const model = Immutable.Map({
            "aaaa": new ModelFile({name: "aaaa", state: ModelFile.State.DEFAULT}),
            "tofu": new ModelFile({name: "tofu", state: ModelFile.State.QUEUED}),
            "flower": new ModelFile({name: "flower", state: ModelFile.State.QUEUED}),
            "power": new ModelFile({name: "power", state: ModelFile.State.DOWNLOADING}),
            "max": new ModelFile({name: "max", state: ModelFile.State.DOWNLOADED}),
            "mrx": new ModelFile({name: "mrx", state: ModelFile.State.EXTRACTING}),
            "blueman": new ModelFile({name: "blueman", state: ModelFile.State.EXTRACTED}),
            "spicy": new ModelFile({name: "spicy", state: ModelFile.State.DELETED}),
        });
        mockModelService._files.next(model);

        let count = 0;
        let viewFiles: Immutable.List<ViewFile> = null;
        viewService.files.subscribe({
            next: list => {
                viewFiles = list;
                count++;
            }
        });
        tick();
        expect(count).toBe(1);

        const comparator: ViewFileComparator = function(a: ViewFile, b: ViewFile) {
            // reverse alphabetical order
            return -1 * a.name.localeCompare(b.name);
        };
        viewService.setComparator(comparator);
        tick();

        expect(count).toBe(2);
        expect(viewFiles.size).toBe(8);
        expect(viewFiles.get(0).name).toBe("tofu");
        expect(viewFiles.get(1).name).toBe("spicy");
        expect(viewFiles.get(2).name).toBe("power");
        expect(viewFiles.get(3).name).toBe("mrx");
        expect(viewFiles.get(4).name).toBe("max");
        expect(viewFiles.get(5).name).toBe("flower");
        expect(viewFiles.get(6).name).toBe("blueman");
        expect(viewFiles.get(7).name).toBe("aaaa");
    }));

    it("should clear bulk selection when comparator changes", fakeAsync(() => {
        const fileSelectionService = TestBed.get(FileSelectionService);
        const one = new ViewFile({name: "one"});
        const two = new ViewFile({name: "two"});

        fileSelectionService.setVisibleFiles(Immutable.List<ViewFile>([one, two]));
        fileSelectionService.toggle(one);

        let selectedNames = Immutable.Set<string>();
        fileSelectionService.selectedNames.subscribe(value => selectedNames = value);
        tick();
        expect(selectedNames.toArray()).toEqual(["one"]);

        viewService.setComparator((a: ViewFile, b: ViewFile) => a.name.localeCompare(b.name));
        tick();

        expect(selectedNames.size).toBe(0);
    }));

    it("should restore the stored page size before emitting paging state", fakeAsync(() => {
        spyOn(storageService, "get").and.callFake(key => {
            if (key === StorageKeys.FILES_PAGE_SIZE) {
                return 0;
            }
        });

        viewService = createViewService();

        let latestPageSize = -1;
        viewService.pageSize.subscribe(size => latestPageSize = size);
        tick();

        expect(storageService.get).toHaveBeenCalledWith(StorageKeys.FILES_PAGE_SIZE);
        expect(latestPageSize).toBe(0);
    }));

    it("should ignore unsupported stored page sizes", fakeAsync(() => {
        spyOn(storageService, "get").and.callFake(key => {
            if (key === StorageKeys.FILES_PAGE_SIZE) {
                return 75;
            }
        });

        viewService = createViewService();

        let latestPageSize = -1;
        viewService.pageSize.subscribe(size => latestPageSize = size);
        tick();

        expect(latestPageSize).toBe(50);
    }));

    it("should save the page size to storage when it changes", () => {
        spyOn(storageService, "set");

        viewService.setPageSize(1000);
        expect(storageService.set).toHaveBeenCalledWith(StorageKeys.FILES_PAGE_SIZE, 1000);

        viewService.setPageSize(0);
        expect(storageService.set).toHaveBeenCalledWith(StorageKeys.FILES_PAGE_SIZE, 0);
    });

    it("should page larger file lists and return all files when page size is zero", fakeAsync(() => {
        let latestFiles: Immutable.List<ViewFile> = null;
        let currentPage = -1;

        viewService.filteredFiles.subscribe(list => latestFiles = list);
        viewService.currentPage.subscribe(page => currentPage = page);

        mockModelService._files.next(createModelFiles(600));
        tick();

        expect(latestFiles.size).toBe(50);
        expect(currentPage).toBe(0);

        viewService.setPageSize(1000);
        tick();
        expect(latestFiles.size).toBe(600);
        expect(currentPage).toBe(0);

        viewService.setPageSize(0);
        tick();
        expect(latestFiles.size).toBe(600);
        expect(currentPage).toBe(0);

        viewService.nextPage();
        viewService.prevPage();
        tick();

        expect(latestFiles.size).toBe(600);
        expect(currentPage).toBe(0);
    }));
});
