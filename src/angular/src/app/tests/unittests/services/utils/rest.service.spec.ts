import {fakeAsync, TestBed} from "@angular/core/testing";
import {HttpClientTestingModule, HttpTestingController} from "@angular/common/http/testing";

import {LoggerService} from "../../../../services/utils/logger.service";
import {RestService} from "../../../../services/utils/rest.service";



describe("Testing rest service", () => {
    let restService: RestService;

    let httpMock: HttpTestingController;

    beforeEach(() => {
        TestBed.configureTestingModule({
            imports: [
                HttpClientTestingModule
            ],
            providers: [
                RestService,
                LoggerService,
            ]
        });
        httpMock = TestBed.get(HttpTestingController);

        restService = TestBed.get(RestService);
    });

    it("should create an instance", () => {
        expect(restService).toBeDefined();
    });

    it("should send http GET on sendRequest", fakeAsync(() => {
        let subscriberIndex = 0;
        restService.sendRequest("/server/request").subscribe({
            next: reaction => {
                subscriberIndex++;
                expect(reaction.success).toBe(true);
            }
        });
        httpMock.expectOne("/server/request").flush("success");

        expect(subscriberIndex).toBe(1);
        httpMock.verify();
    }));

    it("should return correct data on sendRequest", fakeAsync(() => {
        let subscriberIndex = 0;
        restService.sendRequest("/server/request").subscribe({
            next: reaction => {
                subscriberIndex++;
                expect(reaction.success).toBe(true);
                expect(reaction.data).toBe("this is some data");
            }
        });
        httpMock.expectOne("/server/request").flush("this is some data");

        expect(subscriberIndex).toBe(1);
        httpMock.verify();
    }));

    it("should get error message on sendRequest error 404", fakeAsync(() => {
        let subscriberIndex = 0;
        restService.sendRequest("/server/request").subscribe({
            next: reaction => {
                subscriberIndex++;
                expect(reaction.success).toBe(false);
                expect(reaction.errorMessage).toBe("Not found");
            }
        });
        httpMock.expectOne("/server/request").flush(
        "Not found",
        {status: 404, statusText: "Bad Request"}
        );

        expect(subscriberIndex).toBe(1);
        httpMock.verify();
    }));

    it("should get error message on sendRequest network error", fakeAsync(() => {
        let subscriberIndex = 0;
        restService.sendRequest("/server/request").subscribe({
            next: reaction => {
                subscriberIndex++;
                expect(reaction.success).toBe(false);
                expect(reaction.errorMessage).toBe("mock error");
            }
        });
        httpMock.expectOne("/server/request").error(new ErrorEvent("mock error"));

        expect(subscriberIndex).toBe(1);
        httpMock.verify();
    }));

    it("should send http POST on post", fakeAsync(() => {
        let subscriberIndex = 0;
        restService.post("/server/request").subscribe({
            next: reaction => {
                subscriberIndex++;
                expect(reaction.success).toBe(true);
            }
        });
        const request = httpMock.expectOne("/server/request");
        expect(request.request.method).toBe("POST");
        request.flush("success");

        expect(subscriberIndex).toBe(1);
        httpMock.verify();
    }));

    it("should send http DELETE on delete", fakeAsync(() => {
        let subscriberIndex = 0;
        restService.delete("/server/request").subscribe({
            next: reaction => {
                subscriberIndex++;
                expect(reaction.success).toBe(true);
            }
        });
        const request = httpMock.expectOne("/server/request");
        expect(request.request.method).toBe("DELETE");
        request.flush("success");

        expect(subscriberIndex).toBe(1);
        httpMock.verify();
    }));

    it("should send http POST on sendPostRequest", fakeAsync(() => {
        let subscriberIndex = 0;
        restService.sendPostRequest("/server/request", {files: ["one", "two"]}).subscribe({
            next: reaction => {
                subscriberIndex++;
                expect(reaction.success).toBe(true);
                expect(reaction.data).toBe("posted");
            }
        });

        const request = httpMock.expectOne("/server/request");
        expect(request.request.method).toBe("POST");
        expect(request.request.body).toEqual({files: ["one", "two"]});
        request.flush("posted");

        expect(subscriberIndex).toBe(1);
        httpMock.verify();
    }));
});
