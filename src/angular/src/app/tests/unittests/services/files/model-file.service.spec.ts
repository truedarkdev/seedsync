import {fakeAsync, TestBed, tick} from "@angular/core/testing";
import {HttpClientTestingModule, HttpTestingController} from "@angular/common/http/testing";

import * as Immutable from "immutable";

import {ModelEventSourceFactory, ModelFileService} from "../../../../services/files/model-file.service";
import {LoggerService} from "../../../../services/utils/logger.service";
import {ModelFile} from "../../../../services/files/model-file";
import {RestService} from "../../../../services/utils/rest.service";


// noinspection JSUnusedLocalSymbols
const DoNothing = {next: reaction => {}};

class FakeEventSource {
    public onerror: (() => void) | null = null;
    public onopen: (() => void) | null = null;
    public readonly close = jasmine.createSpy("close");
    private readonly listeners: {[event: string]: Array<(payload: any) => void>} = {};

    public addEventListener(event: string, listener: (payload: any) => void): void {
        this.listeners[event] = this.listeners[event] || [];
        this.listeners[event].push(listener);
    }

    public emit(event: string, data: any = ""): void {
        (this.listeners[event] || []).forEach(listener => listener({data}));
    }
}


describe("Testing model file service", () => {
    let modelFileService: ModelFileService;
    let httpMock: HttpTestingController;

    beforeEach(() => {
        TestBed.configureTestingModule({
            imports: [
                HttpClientTestingModule
            ],
            providers: [
                LoggerService,
                RestService,
                ModelFileService
            ]
        });

        httpMock = TestBed.get(HttpTestingController);
        modelFileService = TestBed.get(ModelFileService);
    });

    it("should create an instance", () => {
        expect(modelFileService).toBeDefined();
    });

    it("should register all events with the event source", () => {
        expect(modelFileService.getEventNames()).toEqual(
            ["model-init", "model-added", "model-updated", "model-removed"]
        );
    });

    it("should send validate requests to the validate command path", () => {
        const file = new ModelFile({
            file_id: "[\"movies\",\"File.One\"]",
            name: "File.One"
        });

        modelFileService.validate(file).subscribe(DoNothing);

        const request = httpMock.expectOne("/server/command/validate/File.One?file_id=%5B%22movies%22%2C%22File.One%22%5D");
        expect(request.request.method).toBe("POST");
        request.flush("ok");
        httpMock.verify();
    });

    it("should send retry move with an encoded canonical file_id", () => {
        const file = new ModelFile({
            file_id: "[\"movies\",\"File One & Two.mkv\"]",
            name: "File One & Two.mkv"
        });

        modelFileService.retryMove(file).subscribe(DoNothing);

        const request = httpMock.expectOne(
            "/server/command/retry_move/File%2520One%2520%2526%2520Two.mkv" +
            "?file_id=%5B%22movies%22%2C%22File%20One%20%26%20Two.mkv%22%5D"
        );
        expect(request.request.method).toBe("POST");
        request.flush("ok");
        httpMock.verify();
    });

    it("should send correct model on an init event", fakeAsync(() => {
        let count = 0;
        let latestModel: Immutable.Map<string, ModelFile> = null;
        modelFileService.files.subscribe({
            next: modelFiles => {
                count++;
                latestModel = modelFiles;
            }
        });
        tick();
        expect(count).toBe(1);
        expect(latestModel.size).toBe(0);

        let actualModelFiles = [
            {
                name: "File.One",
                is_dir: false,
                local_size: 1234,
                remote_size: 4567,
                download_progress: 42,
                state: "default",
                downloading_speed: 99,
                eta: 54,
                full_path: "/full/path/to/file.one",
                children: []
            }
        ];
        let expectedModelFiles = [
            new ModelFile({
                name: "File.One",
                is_dir: false,
                local_size: 1234,
                remote_size: 4567,
                download_progress: 42,
                state: ModelFile.State.DEFAULT,
                downloading_speed: 99,
                eta: 54,
                full_path: "/full/path/to/file.one",
                children: Immutable.Set<ModelFile>()
            })
        ];
        modelFileService.notifyEvent("model-init", JSON.stringify(actualModelFiles));
        tick();
        expect(count).toBe(2);
        expect(latestModel.size).toBe(1);
        expect(Immutable.is(latestModel.get("File.One"), expectedModelFiles[0])).toBe(true);
    }));

    it("should key parsed model files by file_id when present", fakeAsync(() => {
        let latestModel: Immutable.Map<string, ModelFile> = null;
        modelFileService.files.subscribe({
            next: modelFiles => latestModel = modelFiles
        });
        tick();

        modelFileService.notifyEvent("model-init", JSON.stringify([{
            file_id: "[\"movies\",\"File.One\"]",
            name: "File.One",
            is_dir: false,
            local_size: 1234,
            remote_size: 4567,
            state: "default",
            downloading_speed: 99,
            eta: 54,
            full_path: "/full/path/to/file.one",
            children: []
        }]));
        tick();

        expect(latestModel.has("[\"movies\",\"File.One\"]")).toBe(true);
        expect(latestModel.get("[\"movies\",\"File.One\"]").file_id).toBe("[\"movies\",\"File.One\"]");
    }));

    it("should send correct model on an added event", fakeAsync(() => {
        let initialModelFiles = [
            {
                name: "File.One",
                is_dir: false,
                local_size: 1234,
                remote_size: 4567,
                state: "default",
                downloading_speed: 99,
                eta: 54,
                full_path: "/full/path/to/file.one",
                children: []
            }
        ];
        modelFileService.notifyEvent("model-init", JSON.stringify(initialModelFiles));

        let count = 0;
        let latestModel: Immutable.Map<string, ModelFile> = null;
        modelFileService.files.subscribe({
            next: modelFiles => {
                count++;
                latestModel = modelFiles;
            }
        });
        tick();
        expect(count).toBe(1);
        expect(latestModel.size).toBe(1);

        let addedModelFile = {
            new_file: {
                name: "File.Two",
                is_dir: false,
                local_size: 1234,
                remote_size: 4567,
                state: "default",
                downloading_speed: 99,
                eta: 54,
                full_path: "/full/path/to/file.two",
                children: []
            },
            old_file: {}
        };

        let expectedModelFiles = [
            new ModelFile({
                name: "File.One",
                is_dir: false,
                local_size: 1234,
                remote_size: 4567,
                state: ModelFile.State.DEFAULT,
                downloading_speed: 99,
                eta: 54,
                full_path: "/full/path/to/file.one",
                children: Immutable.Set<ModelFile>()
            }),
            new ModelFile({
                name: "File.Two",
                is_dir: false,
                local_size: 1234,
                remote_size: 4567,
                state: ModelFile.State.DEFAULT,
                downloading_speed: 99,
                eta: 54,
                full_path: "/full/path/to/file.two",
                children: Immutable.Set<ModelFile>()
            })
        ];
        modelFileService.notifyEvent("model-added", JSON.stringify(addedModelFile));
        tick();
        expect(count).toBe(2);
        expect(latestModel.size).toBe(2);
        expect(Immutable.is(latestModel.get("File.One"), expectedModelFiles[0])).toBe(true);
        expect(Immutable.is(latestModel.get("File.Two"), expectedModelFiles[1])).toBe(true);
    }));

    it("should send correct model on a removed event", fakeAsync(() => {
        let initialModelFiles = [
            {
                name: "File.One",
                is_dir: false,
                local_size: 1234,
                remote_size: 4567,
                state: "default",
                downloading_speed: 99,
                eta: 54,
                full_path: "/full/path/to/file.one",
                children: []
            }
        ];
        modelFileService.notifyEvent("model-init", JSON.stringify(initialModelFiles));

        let count = 0;
        let latestModel: Immutable.Map<string, ModelFile> = null;
        modelFileService.files.subscribe({
            next: modelFiles => {
                count++;
                latestModel = modelFiles;
            }
        });
        tick();
        expect(count).toBe(1);
        expect(latestModel.size).toBe(1);

        let removedModelFile = {
            new_file: {},
            old_file: {
                name: "File.One",
                is_dir: false,
                local_size: 1234,
                remote_size: 4567,
                state: "default",
                downloading_speed: 99,
                eta: 54,
                full_path: "/full/path/to/file.one",
                children: []
            }
        };

        modelFileService.notifyEvent("model-removed", JSON.stringify(removedModelFile));
        tick();
        expect(count).toBe(2);
        expect(latestModel.size).toBe(0);
    }));

    it("should send correct model on an updated event", fakeAsync(() => {
        let initialModelFiles = [
            {
                name: "File.One",
                is_dir: false,
                local_size: 1234,
                remote_size: 4567,
                state: "default",
                downloading_speed: 99,
                eta: 54,
                full_path: "/full/path/to/file.one",
                children: []
            }
        ];
        modelFileService.notifyEvent("model-init", JSON.stringify(initialModelFiles));

        let count = 0;
        let latestModel: Immutable.Map<string, ModelFile> = null;
        modelFileService.files.subscribe({
            next: modelFiles => {
                count++;
                latestModel = modelFiles;
            }
        });
        tick();
        expect(count).toBe(1);
        expect(latestModel.size).toBe(1);

        let updatedModelFile = {
            new_file: {
                name: "File.One",
                is_dir: false,
                local_size: 4567,
                remote_size: 9012,
                state: "downloading",
                downloading_speed: 55,
                eta: 1,
                full_path: "/new/path/to/file.one",
                children: []
            },
            old_file: {
                name: "File.One",
                is_dir: false,
                local_size: 1234,
                remote_size: 4567,
                state: "default",
                downloading_speed: 99,
                eta: 54,
                full_path: "/full/path/to/file.one",
                children: []
            }
        };

        let expectedModelFiles = [
            new ModelFile({
                name: "File.One",
                is_dir: false,
                local_size: 4567,
                remote_size: 9012,
                state: ModelFile.State.DOWNLOADING,
                downloading_speed: 55,
                eta: 1,
                full_path: "/new/path/to/file.one",
                children: Immutable.Set<ModelFile>()
            })
        ];
        modelFileService.notifyEvent("model-updated", JSON.stringify(updatedModelFile));
        tick();
        expect(count).toBe(2);
        expect(latestModel.size).toBe(1);
        expect(Immutable.is(latestModel.get("File.One"), expectedModelFiles[0])).toBe(true);
    }));

    it("should preserve explicit false signals through init and update events", fakeAsync(() => {
        const explicitFalse = {
            name: "empty-dir",
            is_dir: true,
            local_size: 0,
            remote_size: 0,
            remote_present: false,
            local_present: false,
            remote_has_transferable_content: false,
            state: "default",
            children: []
        };
        let latestModel: Immutable.Map<string, ModelFile> = null;
        modelFileService.files.subscribe(model => latestModel = model);
        modelFileService.notifyEvent("model-init", JSON.stringify([explicitFalse]));
        tick();
        let file = latestModel.get("empty-dir");
        expect(file.remote_present).toBe(false);
        expect(file.local_present).toBe(false);
        expect(file.remote_has_transferable_content).toBe(false);

        modelFileService.notifyEvent("model-updated", JSON.stringify({
            old_file: explicitFalse,
            new_file: Object.assign({}, explicitFalse, {remote_size: 99, local_size: 99})
        }));
        tick();
        file = latestModel.get("empty-dir");
        expect(file.remote_present).toBe(false);
        expect(file.local_present).toBe(false);
        expect(file.remote_has_transferable_content).toBe(false);
    }));

    it("should apply target trace metadata without changing model semantics", fakeAsync(() => {
        let latestModel: Immutable.Map<string, ModelFile> = null;
        modelFileService.files.subscribe(model => latestModel = model);
        modelFileService.notifyEvent("model-init", JSON.stringify([{
            file_id: "target",
            name: "target.bin",
            is_dir: false,
            state: "downloading",
            download_progress: 12,
            transferred_size: 100,
            children: []
        }]));
        modelFileService.notifyEvent("model-updated", JSON.stringify({
            trace: {
                cycle: 3,
                corr_id: "stop-resume:target:3",
                stream_emit_sequence: 1
            },
            old_file: {file_id: "target", name: "target.bin", state: "downloading", children: []},
            new_file: {
                file_id: "target",
                name: "target.bin",
                state: "downloading",
                download_progress: 25,
                transferred_size: 200,
                children: []
            }
        }));
        tick();

        expect(latestModel.get("target").download_progress).toBe(25);
        expect(latestModel.get("target").transferred_size).toBe(200);
    }));

    it("should send empty model on disconnect", fakeAsync(() => {
        let count = 0;
        let latestModel: Immutable.Map<string, ModelFile> = null;
        modelFileService.files.subscribe({
            next: modelFiles => {
                count++;
                latestModel = modelFiles;
            }
        });
        tick();
        expect(count).toBe(1);
        expect(latestModel.size).toBe(0);

        modelFileService.notifyDisconnected();
        tick();
        expect(count).toBe(2);
        expect(latestModel.size).toBe(0);

        tick(4000);
    }));

    it("should ignore malformed init payloads", fakeAsync(() => {
        spyOn(console, "error");
        let count = 0;
        let latestModel: Immutable.Map<string, ModelFile> = null;
        modelFileService.files.subscribe({
            next: modelFiles => {
                count++;
                latestModel = modelFiles;
            }
        });
        tick();

        expect(() => modelFileService.notifyEvent("model-init", "{bad json")).not.toThrow();
        tick();

        expect(count).toBe(1);
        expect(latestModel.size).toBe(0);
        expect(console.error).toHaveBeenCalled();
    }));

    it("should ignore malformed update payloads", fakeAsync(() => {
        spyOn(console, "error");
        modelFileService.notifyEvent("model-init", JSON.stringify([{
            name: "File.One",
            is_dir: false,
            local_size: 1234,
            remote_size: 4567,
            state: "default",
            downloading_speed: 99,
            eta: 54,
            full_path: "/full/path/to/file.one",
            children: []
        }]));

        let latestModel: Immutable.Map<string, ModelFile> = null;
        modelFileService.files.subscribe({
            next: modelFiles => latestModel = modelFiles
        });
        tick();

        expect(() => modelFileService.notifyEvent("model-updated", "{\"new_file\":null}")).not.toThrow();
        tick();

        expect(latestModel.size).toBe(1);
        expect(console.error).toHaveBeenCalled();
    }));

    it("should send a POST on queue command", fakeAsync(() => {
        // Connect the service
        modelFileService.notifyConnected();

        let modelFile = new ModelFile({
            name: "File.One",
            is_dir: false,
            local_size: 4567,
            remote_size: 9012,
            state: ModelFile.State.DOWNLOADING,
            downloading_speed: 55,
            eta: 1,
            full_path: "/new/path/to/file.one",
            children: Immutable.Set<ModelFile>()
        });

        let count = 0;
        modelFileService.queue(modelFile).subscribe({
            next: reaction => {
                expect(reaction.success).toBe(true);
                count++;
            }
        });
        const request = httpMock.expectOne("/server/command/queue/File.One");
        expect(request.request.method).toBe("POST");
        request.flush("done");

        tick();
        expect(count).toBe(1);
        httpMock.verify();
    }));


    it("should send correct POST requests on queue command", fakeAsync(() => {
        // Connect the service
        modelFileService.notifyConnected();

        let modelFile;

        modelFile = new ModelFile({
            name: "test",
            state: ModelFile.State.DEFAULT,
            children: Immutable.Set<ModelFile>()
        });
        modelFileService.queue(modelFile).subscribe(DoNothing);
        httpMock.expectOne(req => req.url === "/server/command/queue/test" && req.method === "POST").flush("done");

        modelFile = new ModelFile({
            name: "test space",
            state: ModelFile.State.DEFAULT,
            children: Immutable.Set<ModelFile>()
        });
        modelFileService.queue(modelFile).subscribe(DoNothing);
        httpMock.expectOne(req => req.url === "/server/command/queue/test%2520space" && req.method === "POST").flush("done");

        modelFile = new ModelFile({
            name: "test/slash",
            state: ModelFile.State.DEFAULT,
            children: Immutable.Set<ModelFile>()
        });
        modelFileService.queue(modelFile).subscribe(DoNothing);
        httpMock.expectOne(req => req.url === "/server/command/queue/test%252Fslash" && req.method === "POST").flush("done");

        modelFile = new ModelFile({
            name: "test\"doublequote",
            state: ModelFile.State.DEFAULT,
            children: Immutable.Set<ModelFile>()
        });
        modelFileService.queue(modelFile).subscribe(DoNothing);
        httpMock.expectOne(req => req.url === "/server/command/queue/test%2522doublequote" && req.method === "POST").flush("done");

        modelFile = new ModelFile({
            name: "/test/leadingslash",
            state: ModelFile.State.DEFAULT,
            children: Immutable.Set<ModelFile>()
        });
        modelFileService.queue(modelFile).subscribe(DoNothing);
        httpMock.expectOne(req => req.url === "/server/command/queue/%252Ftest%252Fleadingslash" && req.method === "POST").flush("done");
    }));

    it("should append file_id query parameter on queue command when present", fakeAsync(() => {
        modelFileService.notifyConnected();

        const modelFile = new ModelFile({
            file_id: "[\"movies\",\"File.One\"]",
            name: "File.One",
            state: ModelFile.State.DEFAULT,
            children: Immutable.Set<ModelFile>()
        });

        modelFileService.queue(modelFile).subscribe(DoNothing);
        httpMock.expectOne(req =>
            req.urlWithParams === "/server/command/queue/File.One?file_id=%5B%22movies%22%2C%22File.One%22%5D"
            && req.method === "POST"
        ).flush("done");
    }));

    it("should send a POST on stop command", fakeAsync(() => {
        // Connect the service
        modelFileService.notifyConnected();

        let modelFile = new ModelFile({
            name: "File.One",
            is_dir: false,
            local_size: 4567,
            remote_size: 9012,
            state: ModelFile.State.DOWNLOADING,
            downloading_speed: 55,
            eta: 1,
            full_path: "/new/path/to/file.one",
            children: Immutable.Set<ModelFile>()
        });

        let count = 0;
        modelFileService.stop(modelFile).subscribe({
            next: reaction => {
                expect(reaction.success).toBe(true);
                count++;
            }
        });
        const request = httpMock.expectOne("/server/command/stop/File.One");
        expect(request.request.method).toBe("POST");
        request.flush("done");

        tick();
        expect(count).toBe(1);
        httpMock.verify();
    }));

    it("should send correct POST requests on stop command", fakeAsync(() => {
        // Connect the service
        modelFileService.notifyConnected();

        let modelFile;

        modelFile = new ModelFile({
            name: "test",
            state: ModelFile.State.DEFAULT,
            children: Immutable.Set<ModelFile>()
        });
        modelFileService.stop(modelFile).subscribe(DoNothing);
        httpMock.expectOne(req => req.url === "/server/command/stop/test" && req.method === "POST").flush("done");

        modelFile = new ModelFile({
            name: "test space",
            state: ModelFile.State.DEFAULT,
            children: Immutable.Set<ModelFile>()
        });
        modelFileService.stop(modelFile).subscribe(DoNothing);
        httpMock.expectOne(req => req.url === "/server/command/stop/test%2520space" && req.method === "POST").flush("done");

        modelFile = new ModelFile({
            name: "test/slash",
            state: ModelFile.State.DEFAULT,
            children: Immutable.Set<ModelFile>()
        });
        modelFileService.stop(modelFile).subscribe(DoNothing);
        httpMock.expectOne(req => req.url === "/server/command/stop/test%252Fslash" && req.method === "POST").flush("done");

        modelFile = new ModelFile({
            name: "test\"doublequote",
            state: ModelFile.State.DEFAULT,
            children: Immutable.Set<ModelFile>()
        });
        modelFileService.stop(modelFile).subscribe(DoNothing);
        httpMock.expectOne(req => req.url === "/server/command/stop/test%2522doublequote" && req.method === "POST").flush("done");

        modelFile = new ModelFile({
            name: "/test/leadingslash",
            state: ModelFile.State.DEFAULT,
            children: Immutable.Set<ModelFile>()
        });
        modelFileService.stop(modelFile).subscribe(DoNothing);
        httpMock.expectOne(req => req.url === "/server/command/stop/%252Ftest%252Fleadingslash" && req.method === "POST").flush("done");
    }));

    it("should send a POST on extract command", fakeAsync(() => {
        // Connect the service
        modelFileService.notifyConnected();

        let modelFile = new ModelFile({
            name: "File.One",
            is_dir: false,
            local_size: 4567,
            remote_size: 9012,
            state: ModelFile.State.DOWNLOADING,
            downloading_speed: 55,
            eta: 1,
            full_path: "/new/path/to/file.one",
            children: Immutable.Set<ModelFile>()
        });

        let count = 0;
        modelFileService.extract(modelFile).subscribe({
            next: reaction => {
                expect(reaction.success).toBe(true);
                count++;
            }
        });
        const request = httpMock.expectOne("/server/command/extract/File.One");
        expect(request.request.method).toBe("POST");
        request.flush("done");

        tick();
        expect(count).toBe(1);
        httpMock.verify();
    }));

    it("should send correct POST requests on extract command", fakeAsync(() => {
        // Connect the service
        modelFileService.notifyConnected();

        let modelFile;

        modelFile = new ModelFile({
            name: "test",
            state: ModelFile.State.DEFAULT,
            children: Immutable.Set<ModelFile>()
        });
        modelFileService.extract(modelFile).subscribe(DoNothing);
        httpMock.expectOne(req => req.url === "/server/command/extract/test" && req.method === "POST").flush("done");

        modelFile = new ModelFile({
            name: "test space",
            state: ModelFile.State.DEFAULT,
            children: Immutable.Set<ModelFile>()
        });
        modelFileService.extract(modelFile).subscribe(DoNothing);
        httpMock.expectOne(req => req.url === "/server/command/extract/test%2520space" && req.method === "POST").flush("done");

        modelFile = new ModelFile({
            name: "test/slash",
            state: ModelFile.State.DEFAULT,
            children: Immutable.Set<ModelFile>()
        });
        modelFileService.extract(modelFile).subscribe(DoNothing);
        httpMock.expectOne(req => req.url === "/server/command/extract/test%252Fslash" && req.method === "POST").flush("done");

        modelFile = new ModelFile({
            name: "test\"doublequote",
            state: ModelFile.State.DEFAULT,
            children: Immutable.Set<ModelFile>()
        });
        modelFileService.extract(modelFile).subscribe(DoNothing);
        httpMock.expectOne(req => req.url === "/server/command/extract/test%2522doublequote" && req.method === "POST").flush("done");

        modelFile = new ModelFile({
            name: "/test/leadingslash",
            state: ModelFile.State.DEFAULT,
            children: Immutable.Set<ModelFile>()
        });
        modelFileService.extract(modelFile).subscribe(DoNothing);
        httpMock.expectOne(req => req.url === "/server/command/extract/%252Ftest%252Fleadingslash" && req.method === "POST").flush("done");
    }));

    it("should send a DELETE on delete local command", fakeAsync(() => {
        // Connect the service
        modelFileService.notifyConnected();

        let modelFile = new ModelFile({
            name: "File.One",
            is_dir: false,
            local_size: 4567,
            remote_size: 9012,
            state: ModelFile.State.DOWNLOADING,
            downloading_speed: 55,
            eta: 1,
            full_path: "/new/path/to/file.one",
            children: Immutable.Set<ModelFile>()
        });

        let count = 0;
        modelFileService.deleteLocal(modelFile).subscribe({
            next: reaction => {
                expect(reaction.success).toBe(true);
                count++;
            }
        });
        const request = httpMock.expectOne("/server/command/delete_local/File.One");
        expect(request.request.method).toBe("DELETE");
        request.flush("done");

        tick();
        expect(count).toBe(1);
        httpMock.verify();
    }));

    it("should send correct DELETE requests on delete local command", fakeAsync(() => {
        // Connect the service
        modelFileService.notifyConnected();

        let modelFile;

        modelFile = new ModelFile({
            name: "test",
            state: ModelFile.State.DEFAULT,
            children: Immutable.Set<ModelFile>()
        });
        modelFileService.deleteLocal(modelFile).subscribe(DoNothing);
        httpMock.expectOne(req => req.url === "/server/command/delete_local/test" && req.method === "DELETE").flush("done");

        modelFile = new ModelFile({
            name: "test space",
            state: ModelFile.State.DEFAULT,
            children: Immutable.Set<ModelFile>()
        });
        modelFileService.deleteLocal(modelFile).subscribe(DoNothing);
        httpMock.expectOne(req => req.url === "/server/command/delete_local/test%2520space" && req.method === "DELETE").flush("done");

        modelFile = new ModelFile({
            name: "test/slash",
            state: ModelFile.State.DEFAULT,
            children: Immutable.Set<ModelFile>()
        });
        modelFileService.deleteLocal(modelFile).subscribe(DoNothing);
        httpMock.expectOne(req => req.url === "/server/command/delete_local/test%252Fslash" && req.method === "DELETE").flush("done");

        modelFile = new ModelFile({
            name: "test\"doublequote",
            state: ModelFile.State.DEFAULT,
            children: Immutable.Set<ModelFile>()
        });
        modelFileService.deleteLocal(modelFile).subscribe(DoNothing);
        httpMock.expectOne(req => req.url === "/server/command/delete_local/test%2522doublequote" && req.method === "DELETE").flush("done");

        modelFile = new ModelFile({
            name: "/test/leadingslash",
            state: ModelFile.State.DEFAULT,
            children: Immutable.Set<ModelFile>()
        });
        modelFileService.deleteLocal(modelFile).subscribe(DoNothing);
        httpMock.expectOne(req => req.url === "/server/command/delete_local/%252Ftest%252Fleadingslash" && req.method === "DELETE").flush("done");
    }));

    it("should send a DELETE on delete remote command", fakeAsync(() => {
        // Connect the service
        modelFileService.notifyConnected();

        let modelFile = new ModelFile({
            name: "File.One",
            is_dir: false,
            local_size: 4567,
            remote_size: 9012,
            state: ModelFile.State.DOWNLOADING,
            downloading_speed: 55,
            eta: 1,
            full_path: "/new/path/to/file.one",
            children: Immutable.Set<ModelFile>()
        });

        let count = 0;
        modelFileService.deleteRemote(modelFile).subscribe({
            next: reaction => {
                expect(reaction.success).toBe(true);
                count++;
            }
        });
        const request = httpMock.expectOne("/server/command/delete_remote/File.One");
        expect(request.request.method).toBe("DELETE");
        request.flush("done");

        tick();
        expect(count).toBe(1);
        httpMock.verify();
    }));

    it("should send correct DELETE requests on delete remote command", fakeAsync(() => {
        // Connect the service
        modelFileService.notifyConnected();

        let modelFile;

        modelFile = new ModelFile({
            name: "test",
            state: ModelFile.State.DEFAULT,
            children: Immutable.Set<ModelFile>()
        });
        modelFileService.deleteRemote(modelFile).subscribe(DoNothing);
        httpMock.expectOne(req => req.url === "/server/command/delete_remote/test" && req.method === "DELETE").flush("done");

        modelFile = new ModelFile({
            name: "test space",
            state: ModelFile.State.DEFAULT,
            children: Immutable.Set<ModelFile>()
        });
        modelFileService.deleteRemote(modelFile).subscribe(DoNothing);
        httpMock.expectOne(req => req.url === "/server/command/delete_remote/test%2520space" && req.method === "DELETE").flush("done");

        modelFile = new ModelFile({
            name: "test/slash",
            state: ModelFile.State.DEFAULT,
            children: Immutable.Set<ModelFile>()
        });
        modelFileService.deleteRemote(modelFile).subscribe(DoNothing);
        httpMock.expectOne(req => req.url === "/server/command/delete_remote/test%252Fslash" && req.method === "DELETE").flush("done");

        modelFile = new ModelFile({
            name: "test\"doublequote",
            state: ModelFile.State.DEFAULT,
            children: Immutable.Set<ModelFile>()
        });
        modelFileService.deleteRemote(modelFile).subscribe(DoNothing);
        httpMock.expectOne(req => req.url === "/server/command/delete_remote/test%2522doublequote" && req.method === "DELETE").flush("done");

        modelFile = new ModelFile({
            name: "/test/leadingslash",
            state: ModelFile.State.DEFAULT,
            children: Immutable.Set<ModelFile>()
        });
        modelFileService.deleteRemote(modelFile).subscribe(DoNothing);
        httpMock.expectOne(req => req.url === "/server/command/delete_remote/%252Ftest%252Fleadingslash" && req.method === "DELETE").flush("done");
    }));

    it("automatically chains bounded shallow roots for All without server-side view queries", () => {
        spyOn<any>(modelFileService, "_openStream");
        modelFileService.setPageSize(0);
        modelFileService.activateScope("movies");
        (<any>modelFileService)._handleInitialPage("movies", <any>{data: JSON.stringify({
            records: [{file_id: "one", name: "one", state: "default", children: []}], next_cursor: "one"
        })});
        const second = httpMock.expectOne(req => req.url === "/server/model/v1/pairs/movies/roots" &&
            req.params.get("cursor") === "one" && req.params.get("limit") === "200");
        expect(second.request.params.get("sort")).toBeNull();
        expect(second.request.params.get("status")).toBeNull();
        expect(second.request.params.get("name")).toBeNull();
        second.flush({records: [{file_id: "two", name: "two", state: "default", children: []}], next_cursor: "two"});
        const third = httpMock.expectOne(req => req.url === "/server/model/v1/pairs/movies/roots" &&
            req.params.get("cursor") === "two" && req.params.get("limit") === "200");
        third.flush({records: [{file_id: "three", name: "three", state: "default", children: []}], next_cursor: null});

        let count = 0;
        modelFileService.files.subscribe(files => count = files.size);
        expect(count).toBe(3);
        httpMock.expectNone(req => req.url.indexOf("/children") >= 0);
        httpMock.verify();
    });

    it("accumulates every root with immutable identity deduplication before finite view pages render", () => {
        spyOn<any>(modelFileService, "_openStream");
        modelFileService.setPageSize(25);
        modelFileService.activateScope("movies");
        const chunk = (prefix: string) => Array.from({length: 200}, (_value, index) => ({
            file_id: `${prefix}-${index}`, name: `${prefix}-${index}`, state: "default", children: []
        }));
        (<any>modelFileService)._handleInitialPage("movies", <any>{data: JSON.stringify({records: chunk("a"), next_cursor: "a"})});
        const middle = httpMock.expectOne(req => req.params.get("cursor") === "a" && req.params.get("limit") === "200");
        middle.flush({records: [{file_id: "a-0", name: "a-0 updated", state: "queued", children: []}].concat(chunk("b")), next_cursor: "b"});
        const last = httpMock.expectOne(req => req.params.get("cursor") === "b" && req.params.get("limit") === "200");
        last.flush({records: chunk("c").slice(0, 100), next_cursor: null});
        let files: Immutable.Map<string, ModelFile> = null;
        modelFileService.files.subscribe(value => files = value);
        expect(files.size).toBe(500);
        expect(files.get("a-0").name).toBe("a-0 updated");
        httpMock.verify();
    });

    it("applies transfer patches without a full root reload and resolves initial-chain races to the latest root", () => {
        const stream = new FakeEventSource();
        spyOn(ModelEventSourceFactory, "create").and.returnValue(<any>stream);
        modelFileService.activateScope("movies");
        stream.emit("model-page", JSON.stringify({
            records: [{file_id: "root-a", name: "root-a", state: "downloading", children: []}], next_cursor: "next"
        }));
        stream.emit("model-invalidate", JSON.stringify({
            records: [{file_id: "root-a", name: "root-a", state: "downloaded", children: []}], removed_file_ids: []
        }));
        stream.emit("model-invalidate", JSON.stringify({records: [], removed_file_ids: ["root-b"]}));
        httpMock.expectOne(req => req.params.get("cursor") === "next")
            .flush({records: [{file_id: "root-b", name: "root-b", state: "default", children: []}], next_cursor: null});
        let files: Immutable.Map<string, ModelFile> = null;
        modelFileService.files.subscribe(value => files = value);
        expect(files.get("root-a").state).toBe(ModelFile.State.DOWNLOADED);
        expect(files.has("root-b")).toBe(false);
        stream.emit("model-invalidate", JSON.stringify({
            records: [{file_id: "root-a", name: "root-a child update", state: "queued", children: []}], removed_file_ids: []
        }));
        expect(files.get("root-a").name).toBe("root-a child update");
        httpMock.expectNone(req => req.url.endsWith("/roots"));
        httpMock.verify();
    });

    it("applies live compact summary counts and closes the summary stream after its final consumer", () => {
        const summarySource = new FakeEventSource();
        spyOn(ModelEventSourceFactory, "create").and.returnValue(<any>summarySource);
        let counts: {[key: string]: number} = null;
        modelFileService.visibleStateCounts.subscribe(value => counts = value);
        (<any>modelFileService)._scopeId = "movies";

        modelFileService.startSummaryStream();
        httpMock.expectNone("/server/model/v1/summary");
        expect(ModelEventSourceFactory.create).toHaveBeenCalledWith("/server/model/v1/summary/stream");

        summarySource.emit("model-summary", JSON.stringify({model_version: 2, path_pairs: [
            {path_pair_id: "movies", visible_state_counts: {queued: 2, stopped: 1}}
        ]}));
        expect(counts).toEqual({queued: 2, stopped: 1});

        modelFileService.stopSummaryStream();
        expect(summarySource.close).toHaveBeenCalled();
        httpMock.verify();
    });

    it("keeps newer summary SSE data when an older explicit refresh resolves late or after stop", () => {
        let summaries: any[] = null;
        modelFileService.summaries.subscribe(value => summaries = value);
        modelFileService.refreshSummary();
        const refresh = httpMock.expectOne("/server/model/v1/summary");
        const source = new FakeEventSource();
        spyOn(ModelEventSourceFactory, "create").and.returnValue(<any>source);
        modelFileService.startSummaryStream();
        source.emit("model-summary", JSON.stringify({model_version: 3, path_pairs: [{path_pair_id: "movies", root_count: 3}]}));
        refresh.flush({model_version: 2, path_pairs: [{path_pair_id: "movies", root_count: 2}]});
        expect(summaries[0].root_count).toBe(3);

        modelFileService.stopSummaryStream();
        modelFileService.refreshSummary();
        const stoppedRefresh = httpMock.expectOne("/server/model/v1/summary");
        modelFileService.stopSummaryStream();
        stoppedRefresh.flush({model_version: 4, path_pairs: [{path_pair_id: "movies", root_count: 4}]});
        expect(summaries[0].root_count).toBe(3);
        httpMock.verify();
    });

    it("rejects stale summaries after an error until the current stream reconnects", () => {
        const first = new FakeEventSource();
        const second = new FakeEventSource();
        spyOn(ModelEventSourceFactory, "create").and.returnValues(<any>first, <any>second);
        let summaries: any[] = null;
        modelFileService.summaries.subscribe(value => summaries = value);
        modelFileService.startSummaryStream();
        first.emit("model-summary", JSON.stringify({model_version: 8, path_pairs: [
            {path_pair_id: "pair-b", local_library_state: "up_to_date"}
        ]}));
        first.onerror!();
        first.emit("model-summary", JSON.stringify({model_version: 7, path_pairs: [
            {path_pair_id: "pair-b", local_library_state: "stale"}
        ]}));
        expect(summaries[0].local_library_state).toBe("up_to_date");
        first.onopen!();
        first.emit("model-summary", JSON.stringify({model_version: 1, path_pairs: [{path_pair_id: "movies", root_count: 1}]}));
        expect(summaries[0].root_count).toBe(1);

        modelFileService.stopSummaryStream();
        modelFileService.startSummaryStream();
        first.emit("model-summary", JSON.stringify({model_version: 99, path_pairs: [{path_pair_id: "movies", root_count: 99}]}));
        second.emit("model-summary", JSON.stringify({model_version: 2, path_pairs: [{path_pair_id: "movies", root_count: 2}]}));
        expect(summaries[0].root_count).toBe(2);
        httpMock.verify();
    });

    it("retries one recoverable cursor failure with a bounded delay", fakeAsync(() => {
        const initial = new FakeEventSource();
        const retry = new FakeEventSource();
        spyOn(ModelEventSourceFactory, "create").and.returnValues(<any>initial, <any>retry);
        modelFileService.setPageSize(25);
        modelFileService.activateScope("movies");
        initial.emit("model-page", JSON.stringify({records: [], next_cursor: "stale"}));
        httpMock.expectOne(req => req.url.endsWith("/pairs/movies/roots") && req.params.get("cursor") === "stale")
            .flush({}, {status: 409, statusText: "Cursor conflict"});
        tick(250);
        expect(initial.close).toHaveBeenCalled();
        retry.emit("model-page", JSON.stringify({records: [], next_cursor: null}));
        httpMock.verify();
    }));

    it("stops after the bounded retry budget for repeated cursor or server failures", fakeAsync(() => {
        const first = new FakeEventSource();
        const second = new FakeEventSource();
        const factory = spyOn(ModelEventSourceFactory, "create").and.returnValues(<any>first, <any>second);
        modelFileService.activateScope("movies");
        first.emit("model-page", JSON.stringify({records: [], next_cursor: "first"}));
        httpMock.expectOne(req => req.params.get("cursor") === "first")
            .flush({}, {status: 409, statusText: "Cursor conflict"});
        tick(250);
        second.emit("model-page", JSON.stringify({records: [], next_cursor: "second"}));
        httpMock.expectOne(req => req.params.get("cursor") === "second")
            .flush({}, {status: 503, statusText: "Unavailable"});
        tick(500);
        expect(factory.calls.count()).toBe(2);
        expect((<any>modelFileService)._pendingRecords.size).toBe(0);
        expect((<any>modelFileService)._pendingRemoved.size).toBe(0);
        httpMock.verify();
    }));

    it("stops after the bounded retry budget for persistently malformed initial pages", fakeAsync(() => {
        const first = new FakeEventSource();
        const second = new FakeEventSource();
        const factory = spyOn(ModelEventSourceFactory, "create").and.returnValues(<any>first, <any>second);
        modelFileService.activateScope("movies");
        first.emit("model-page", "not-json");
        tick(250);
        second.emit("model-page", "still-not-json");
        tick(500);
        expect(factory.calls.count()).toBe(2);
        httpMock.verify();
    }));

    it("ignores stale callbacks from a closed stream for the same scope", () => {
        const first = new FakeEventSource();
        const second = new FakeEventSource();
        spyOn(ModelEventSourceFactory, "create").and.returnValues(<any>first, <any>second);
        modelFileService.activateScope("movies");
        first.emit("model-reset");
        first.emit("model-page", JSON.stringify({
            records: [{file_id: "stale", name: "stale", state: "default", children: []}], next_cursor: null
        }));
        second.emit("model-page", JSON.stringify({
            records: [{file_id: "fresh", name: "fresh", state: "default", children: []}], next_cursor: null
        }));
        let files: Immutable.Map<string, ModelFile> = null;
        modelFileService.files.subscribe(value => files = value);
        expect(files.has("stale")).toBe(false);
        expect(files.has("fresh")).toBe(true);
        httpMock.verify();
    });

    it("keeps a newer continuation page over an older pending patch", () => {
        const stream = new FakeEventSource();
        spyOn(ModelEventSourceFactory, "create").and.returnValue(<any>stream);
        modelFileService.activateScope("movies");
        stream.emit("model-page", JSON.stringify({model_version: 2,
            records: [{file_id: "root", name: "initial", state: "downloading", children: []}], next_cursor: "next"}));
        stream.emit("model-invalidate", JSON.stringify({model_version: 2,
            records: [{file_id: "root", name: "older patch", state: "downloaded", children: []}], removed_file_ids: []}));
        httpMock.expectOne(req => req.params.get("cursor") === "next").flush({model_version: 3,
            records: [{file_id: "root", name: "newer page", state: "queued", children: []}], next_cursor: null});
        let files: Immutable.Map<string, ModelFile> = null;
        modelFileService.files.subscribe(value => files = value);
        expect(files.get("root").name).toBe("newer page");
        httpMock.verify();
    });

    it("applies a newer patch after an earlier page", () => {
        const stream = new FakeEventSource();
        spyOn(ModelEventSourceFactory, "create").and.returnValue(<any>stream);
        modelFileService.activateScope("movies");
        stream.emit("model-page", JSON.stringify({model_version: 2,
            records: [{file_id: "root", name: "page", state: "downloading", children: []}], next_cursor: null}));
        stream.emit("model-invalidate", JSON.stringify({model_version: 3,
            records: [{file_id: "root", name: "newer patch", state: "downloaded", children: []}], removed_file_ids: []}));
        let files: Immutable.Map<string, ModelFile> = null;
        modelFileService.files.subscribe(value => files = value);
        expect(files.get("root").name).toBe("newer patch");
        httpMock.verify();
    });
});
