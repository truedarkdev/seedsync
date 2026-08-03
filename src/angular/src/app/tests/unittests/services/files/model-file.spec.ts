import * as Immutable from "immutable";

import {ModelFile} from "../../../../services/files/model-file";

describe("Testing model file initialization", () => {
    let baseJson;
    let baseModelFile;

    beforeEach(() => {
        baseJson = {
            name: "File.One",
            is_dir: false,
            local_size: 1234,
            remote_size: 4567,
            transferred_size: 1234,
            download_progress: 42,
            state: "default",
            downloading_speed: 99,
            eta: 54,
            full_path: "/full/path/to/file.one",
            is_extractable: true,
            local_created_timestamp: "1541828418.0",
            local_modified_timestamp: "1541828418.9439101",
            remote_created_timestamp: "1541828418.0",
            remote_modified_timestamp: "1541828418.9439101",
            is_stoppable: true,
            children: []
        };
        baseModelFile = ModelFile.fromJson(baseJson);
    });

    it("should be immutable", () => {
        expect(baseModelFile instanceof Immutable.Record).toBe(true);
    });

    it("should have an immutable container of children", () => {
        expect(baseModelFile.children instanceof Immutable.Set).toBe(true);
    });

    it("should correctly initialize all states", () => {
        baseJson.state = "default";
        baseModelFile = ModelFile.fromJson(baseJson);
        expect(baseModelFile.state).toBe(ModelFile.State.DEFAULT);
        baseJson.state = "queued";
        baseModelFile = ModelFile.fromJson(baseJson);
        expect(baseModelFile.state).toBe(ModelFile.State.QUEUED);
        baseJson.state = "downloading";
        baseModelFile = ModelFile.fromJson(baseJson);
        expect(baseModelFile.state).toBe(ModelFile.State.DOWNLOADING);
        baseJson.state = "downloaded";
        baseModelFile = ModelFile.fromJson(baseJson);
        expect(baseModelFile.state).toBe(ModelFile.State.DOWNLOADED);
        baseJson.state = "deleted";
        baseModelFile = ModelFile.fromJson(baseJson);
        expect(baseModelFile.state).toBe(ModelFile.State.DELETED);
        baseJson.state = "extracting";
        baseModelFile = ModelFile.fromJson(baseJson);
        expect(baseModelFile.state).toBe(ModelFile.State.EXTRACTING);
        baseJson.state = "extracted";
        baseModelFile = ModelFile.fromJson(baseJson);
        expect(baseModelFile.state).toBe(ModelFile.State.EXTRACTED);
    });

    it("should initialize with correct values", () => {
        expect(baseModelFile.name).toBe("File.One");
        expect(baseModelFile.is_dir).toBe(false);
        expect(baseModelFile.local_size).toBe(1234);
        expect(baseModelFile.remote_size).toBe(4567);
        expect(baseModelFile.transferred_size).toBe(1234);
        expect(baseModelFile.download_progress).toBe(42);
        expect(baseModelFile.state).toBe(ModelFile.State.DEFAULT);
        expect(baseModelFile.downloading_speed).toBe(99);
        expect(baseModelFile.eta).toBe(54);
        expect(baseModelFile.full_path).toBe("/full/path/to/file.one");
        expect(baseModelFile.is_extractable).toBe(true);
        expect(baseModelFile.is_stoppable).toBe(true);
        expect(baseModelFile.local_created_timestamp).toEqual(new Date("November 9, 2018 21:40:18 PST"));
        expect(baseModelFile.local_modified_timestamp).toEqual(new Date(1541828418943));
        expect(baseModelFile.remote_created_timestamp).toEqual(new Date("November 9, 2018 21:40:18 PST"));
        expect(baseModelFile.remote_modified_timestamp).toEqual(new Date(1541828418943));
        expect(baseModelFile.children.size).toBe(0);
    });

    it("should initialize null timestamps correctly", () => {
        baseJson.local_created_timestamp = null;
        baseModelFile = ModelFile.fromJson(baseJson);
        expect(baseModelFile.local_created_timestamp).toBeNull();
        baseJson.local_modified_timestamp = null;
        baseModelFile = ModelFile.fromJson(baseJson);
        expect(baseModelFile.local_modified_timestamp).toBeNull();
        baseJson.remote_created_timestamp = null;
        baseModelFile = ModelFile.fromJson(baseJson);
        expect(baseModelFile.remote_created_timestamp).toBeNull();
        baseJson.remote_modified_timestamp = null;
        baseModelFile = ModelFile.fromJson(baseJson);
        expect(baseModelFile.remote_modified_timestamp).toBeNull();
    });

    it("should parse nullable downloaded timestamps and reject malformed values", () => {
        baseJson.downloaded_timestamp = "1541828418.0";
        baseModelFile = ModelFile.fromJson(baseJson);
        expect(baseModelFile.downloaded_timestamp).toEqual(new Date(1541828418000));

        baseJson.downloaded_timestamp = "not-a-time";
        baseModelFile = ModelFile.fromJson(baseJson);
        expect(baseModelFile.downloaded_timestamp).toBeNull();

        delete baseJson.downloaded_timestamp;
        baseModelFile = ModelFile.fromJson(baseJson);
        expect(baseModelFile.downloaded_timestamp).toBeNull();
    });

    it("should initialize null sizes correctly", () => {
        baseJson.local_size = null;
        baseJson.remote_size = null;
        baseModelFile = ModelFile.fromJson(baseJson);
        expect(baseModelFile.local_size).toBeNull();
        expect(baseModelFile.remote_size).toBeNull();
    });

    it("should preserve explicit false presence/content signals despite contradictory sizes", () => {
        const direct = new ModelFile({
            name: "explicit-direct",
            is_dir: false,
            local_size: 0,
            remote_size: 0,
            remote_present: false,
            local_present: false,
            remote_has_transferable_content: false,
        });
        expect(direct.remote_present).toBe(false);
        expect(direct.local_present).toBe(false);
        expect(direct.remote_has_transferable_content).toBe(false);

        const json = {
            name: "explicit-json",
            is_dir: false,
            local_size: 0,
            remote_size: 0,
            remote_present: false,
            local_present: false,
            remote_has_transferable_content: false,
            state: "default",
            children: []
        };
        const parsed = ModelFile.fromJson(json);
        expect(parsed.remote_present).toBe(false);
        expect(parsed.local_present).toBe(false);
        expect(parsed.remote_has_transferable_content).toBe(false);

        const initialized = new ModelFile(parsed);
        expect(initialized.remote_present).toBe(false);
        expect(initialized.local_present).toBe(false);
        expect(initialized.remote_has_transferable_content).toBe(false);
    });

    it("should recursively classify legacy remote children and zero-byte files", () => {
        const empty = ModelFile.fromJson({
            name: "empty",
            is_dir: true,
            remote_size: 0,
            local_size: null,
            state: "default",
            children: [{
                name: "nested-empty",
                is_dir: true,
                remote_size: 0,
                local_size: null,
                state: "default",
                children: []
            }]
        });
        expect(empty.remote_present).toBe(true);
        expect(empty.remote_has_transferable_content).toBe(false);

        const nestedZero = ModelFile.fromJson({
            name: "nested-zero",
            is_dir: true,
            remote_size: 0,
            local_size: null,
            state: "default",
            children: [{
                name: "nested",
                is_dir: true,
                remote_size: 0,
                local_size: null,
                state: "default",
                children: [{
                    name: "zero.bin",
                    is_dir: false,
                    remote_size: 0,
                    local_size: null,
                    state: "default",
                    children: []
                }]
            }]
        });
        expect(nestedZero.remote_has_transferable_content).toBe(true);
        expect(nestedZero.children.first().children.first().remote_has_transferable_content).toBe(true);
    });

    it("should correctly initialize children", () => {
        baseJson.children = [
            {
                name: "a",
                is_dir: true,
                local_size: 1,
                remote_size: 11,
                transferred_size: 1,
                download_progress: 11,
                state: "default",
                downloading_speed: 111,
                eta: 1111,
                full_path: "root/a",
                is_extractable: true,
                is_stoppable: false,
                children: [
                    {
                        name: "aa",
                        is_dir: false,
                        local_size: 1,
                        remote_size: 11,
                        transferred_size: 1,
                        download_progress: 12,
                        state: "default",
                        downloading_speed: 111,
                        eta: 1111,
                        full_path: "root/a/aa",
                        is_extractable: true,
                        is_stoppable: true,
                        children: []
                    },
                ]
            },
            {
                name: "b",
                is_dir: false,
                local_size: 2,
                remote_size: 22,
                transferred_size: 2,
                download_progress: 22,
                state: "default",
                downloading_speed: 222,
                eta: 2222,
                full_path: "root/b",
                is_extractable: false,
                is_stoppable: false,
                children: []
            }
        ];
        baseModelFile = ModelFile.fromJson(baseJson);
        expect(baseModelFile.children.size).toBe(2);

        let a = baseModelFile.children.find(value => {return value.name === "a"});
        expect(a.name).toBe("a");
        expect(a.is_dir).toBe(true);
        expect(a.local_size).toBe(1);
        expect(a.remote_size).toBe(11);
        expect(a.transferred_size).toBe(1);
        expect(a.download_progress).toBe(11);
        expect(a.state).toBe(ModelFile.State.DEFAULT);
        expect(a.downloading_speed).toBe(111);
        expect(a.eta).toBe(1111);
        expect(a.full_path).toBe("root/a");
        expect(a.is_extractable).toBe(true);
        expect(a.is_stoppable).toBe(false);
        expect(a.children.size).toBe(1);

        let aa = a.children.find(value => {return value.name === "aa"});
        expect(aa.name).toBe("aa");
        expect(aa.is_dir).toBe(false);
        expect(aa.local_size).toBe(1);
        expect(aa.remote_size).toBe(11);
        expect(aa.transferred_size).toBe(1);
        expect(aa.download_progress).toBe(12);
        expect(aa.state).toBe(ModelFile.State.DEFAULT);
        expect(aa.downloading_speed).toBe(111);
        expect(aa.eta).toBe(1111);
        expect(aa.full_path).toBe("root/a/aa");
        expect(aa.is_extractable).toBe(true);
        expect(aa.is_stoppable).toBe(true);
        expect(aa.children.size).toBe(0);

        let b = baseModelFile.children.find(value => {return value.name === "b"});
        expect(b.name).toBe("b");
        expect(b.is_dir).toBe(false);
        expect(b.local_size).toBe(2);
        expect(b.remote_size).toBe(22);
        expect(b.transferred_size).toBe(2);
        expect(b.download_progress).toBe(22);
        expect(b.state).toBe(ModelFile.State.DEFAULT);
        expect(b.downloading_speed).toBe(222);
        expect(b.eta).toBe(2222);
        expect(b.full_path).toBe("root/b");
        expect(b.is_extractable).toBe(false);
        expect(b.is_stoppable).toBe(false);
        expect(b.children.size).toBe(0);
    });
});
