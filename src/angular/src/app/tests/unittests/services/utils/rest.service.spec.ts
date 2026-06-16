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

    it("should redact config response logging", fakeAsync(() => {
        const debugSpy = spyOn(console, "debug");
        const responseBody = JSON.stringify({
            lftp: {
                remote_password: "supersecret"
            }
        });

        restService.sendRequest("/server/config/get").subscribe({
            next: reaction => {
                expect(reaction.success).toBe(true);
                expect(reaction.data).toBe(responseBody);
            }
        });
        httpMock.expectOne("/server/config/get").flush(responseBody);

        expect(debugSpy).toHaveBeenCalledWith("%s http response: %s", "/server/config/get", "[redacted]");
        expect(JSON.stringify(debugSpy.calls.allArgs())).not.toContain("supersecret");
        httpMock.verify();
    }));

    it("should redact config set response logging too", fakeAsync(() => {
        const debugSpy = spyOn(console, "debug");
        const responseBody = "lftp.remote_password set to supersecret";

        restService.sendPostRequest("/server/config/set/lftp/remote_password", {value: "supersecret"}).subscribe({
            next: reaction => {
                expect(reaction.success).toBe(true);
                expect(reaction.data).toBe(responseBody);
            }
        });
        const request = httpMock.expectOne("/server/config/set/lftp/remote_password");
        expect(request.request.method).toBe("POST");
        expect(request.request.body).toEqual({value: "supersecret"});
        request.flush(responseBody);

        expect(debugSpy).toHaveBeenCalledWith(
            "%s http response: %s",
            "/server/config/set/lftp/remote_password",
            "[redacted]"
        );
        expect(JSON.stringify(debugSpy.calls.allArgs())).not.toContain("supersecret");
        httpMock.verify();
    }));

    it("should redact config error logging", fakeAsync(() => {
        const debugSpy = spyOn(console, "debug");
        const responseBody = "config-secret=supersecret";

        restService.sendRequest("/server/config/get").subscribe({
            next: reaction => {
                expect(reaction.success).toBe(false);
                expect(reaction.errorMessage).toBe(responseBody);
            }
        });
        httpMock.expectOne("/server/config/get").flush(
            responseBody,
            {status: 404, statusText: "Bad Request"}
        );

        const errorCall = debugSpy.calls.mostRecent();
        expect(errorCall.args).toEqual(["%s error: %s", "/server/config/get", "[redacted]"]);
        expect(JSON.stringify(errorCall.args)).not.toContain("supersecret");
        httpMock.verify();
    }));

    it("should keep raw response logging for non-config endpoints", fakeAsync(() => {
        const debugSpy = spyOn(console, "debug");

        restService.sendRequest("/server/request").subscribe({
            next: reaction => {
                expect(reaction.success).toBe(true);
            }
        });
        httpMock.expectOne("/server/request").flush("success");

        expect(debugSpy).toHaveBeenCalledWith("%s http response: %s", "/server/request", "success");
        httpMock.verify();
    }));

    it("should get error message and keep raw error logging on sendRequest error 404", fakeAsync(() => {
        const debugSpy = spyOn(console, "debug");
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
        const errorCall = debugSpy.calls.mostRecent();
        expect(errorCall.args[0]).toBe("%s error: %O");
        expect(errorCall.args[1]).toBe("/server/request");
        expect(errorCall.args[2].status).toBe(404);
        expect(errorCall.args[2].error).toBe("Not found");
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
