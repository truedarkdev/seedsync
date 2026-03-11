import {TestBed} from "@angular/core/testing";
import {HttpClientTestingModule, HttpTestingController} from "@angular/common/http/testing";

import {LoggerService} from "../../../../services/utils/logger.service";
import {ConnectedService} from "../../../../services/utils/connected.service";
import {StreamServiceRegistry} from "../../../../services/base/stream-service.registry";
import {MockStreamServiceRegistry} from "../../../mocks/mock-stream-service.registry";
import {
    PathPairService,
    PathPair
} from "../../../../services/settings/path-pair.service";


describe("Testing path pair service", () => {
    let mockRegistry: MockStreamServiceRegistry;
    let httpMock: HttpTestingController;
    let pathPairService: PathPairService;

    beforeEach(() => {
        TestBed.configureTestingModule({
            imports: [
                HttpClientTestingModule
            ],
            providers: [
                PathPairService,
                LoggerService,
                ConnectedService,
                {provide: StreamServiceRegistry, useClass: MockStreamServiceRegistry}
            ]
        });

        mockRegistry = TestBed.get(StreamServiceRegistry);
        httpMock = TestBed.get(HttpTestingController);
        pathPairService = TestBed.get(PathPairService);

        mockRegistry.connect();
        pathPairService.onInit();
    });

    it("should create an instance", () => {
        expect(pathPairService).toBeDefined();
    });

    it("should load path pairs on connect", () => {
        const received: PathPair[][] = [];
        pathPairService.pathPairs.subscribe({
            next: pathPairs => received.push(pathPairs)
        });

        httpMock.expectOne("/server/path-pairs").flush({
            success: true,
            data: [{
                id: "movies",
                name: "Movies",
                remote_path: "/remote/movies",
                local_path: "/downloads/movies",
                enabled: true,
                auto_queue: true
            }]
        });

        expect(received[received.length - 1].length).toBe(1);
        expect(received[received.length - 1][0].name).toBe("Movies");
        httpMock.verify();
    });

    it("should include warnings in create responses and refresh the list", () => {
        httpMock.expectOne("/server/path-pairs").flush({success: true, data: []});

        let warnings = [];
        pathPairService.create({
            name: "Movies",
            remote_path: "/remote/movies",
            local_path: "/media/movies",
            enabled: true,
            auto_queue: true
        }).subscribe({
            next: result => warnings = result.warnings
        });

        const createRequest = httpMock.expectOne("/server/path-pairs");
        expect(createRequest.request.method).toBe("POST");
        createRequest.flush({
            success: true,
            data: {
                id: "movies",
                name: "Movies",
                remote_path: "/remote/movies",
                local_path: "/media/movies",
                enabled: true,
                auto_queue: true
            },
            warnings: ["Docker path warning"]
        });

        httpMock.expectOne("/server/path-pairs").flush({success: true, data: []});

        expect(warnings).toEqual(["Docker path warning"]);
        httpMock.verify();
    });

    it("should reorder path pairs and publish the new order", () => {
        httpMock.expectOne("/server/path-pairs").flush({success: true, data: []});

        let latest: PathPair[] = [];
        pathPairService.pathPairs.subscribe({
            next: pathPairs => latest = pathPairs
        });

        pathPairService.reorder(["tv", "movies"]).subscribe();
        const reorderRequest = httpMock.expectOne("/server/path-pairs/reorder");
        expect(reorderRequest.request.method).toBe("POST");
        reorderRequest.flush({
            success: true,
            data: [
                {
                    id: "tv",
                    name: "TV",
                    remote_path: "/remote/tv",
                    local_path: "/downloads/tv",
                    enabled: true,
                    auto_queue: true
                },
                {
                    id: "movies",
                    name: "Movies",
                    remote_path: "/remote/movies",
                    local_path: "/downloads/movies",
                    enabled: true,
                    auto_queue: true
                }
            ]
        });

        expect(latest.map(pathPair => pathPair.id)).toEqual(["tv", "movies"]);
        httpMock.verify();
    });
});
