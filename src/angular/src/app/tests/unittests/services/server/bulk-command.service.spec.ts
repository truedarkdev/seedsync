import {TestBed} from "@angular/core/testing";
import {HttpClientTestingModule, HttpTestingController} from "@angular/common/http/testing";

import {LoggerService} from "../../../../services/utils/logger.service";
import {BulkCommandService} from "../../../../services/server/bulk-command.service";
import {MockStreamServiceRegistry} from "../../../mocks/mock-stream-service.registry";
import {RestService} from "../../../../services/utils/rest.service";
import {ConnectedService} from "../../../../services/utils/connected.service";
import {StreamServiceRegistry} from "../../../../services/base/stream-service.registry";

describe("Testing bulk command service", () => {
    let mockRegistry: MockStreamServiceRegistry;
    let httpMock: HttpTestingController;
    let commandService: BulkCommandService;

    beforeEach(() => {
        TestBed.configureTestingModule({
            imports: [
                HttpClientTestingModule
            ],
            providers: [
                BulkCommandService,
                LoggerService,
                RestService,
                ConnectedService,
                {provide: StreamServiceRegistry, useClass: MockStreamServiceRegistry}
            ]
        });

        mockRegistry = TestBed.get(StreamServiceRegistry);
        httpMock = TestBed.get(HttpTestingController);
        commandService = TestBed.get(BulkCommandService);

        mockRegistry.connect();
        commandService.onInit();
    });

    it("should create an instance", () => {
        expect(commandService).toBeDefined();
    });

    it("should send a POST bulk queue command", () => {
        let count = 0;

        commandService.queue([
            {file_id: "[\"movies\",\"test1\"]", name: "test1"},
            {file_id: "[\"tv\",\"test2\"]", name: "test2"}
        ]).subscribe({
            next: reaction => {
                count++;
                expect(reaction.success).toBe(true);
            }
        });

        const request = httpMock.expectOne("/server/command/bulk/queue");
        expect(request.request.method).toBe("POST");
        expect(request.request.body).toEqual({files: [
            {file_id: "[\"movies\",\"test1\"]", name: "test1"},
            {file_id: "[\"tv\",\"test2\"]", name: "test2"}
        ]});
        request.flush("{}");

        expect(count).toBe(1);
        httpMock.verify();
    });
});
