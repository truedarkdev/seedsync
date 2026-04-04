import {fakeAsync, flushMicrotasks, TestBed} from "@angular/core/testing";

import {DialogRef, Modal} from "../../../../services/utils/modal.service";
import {Localization} from "../../../../common/localization";


describe("Testing modal service", () => {
    let modal: Modal;

    beforeEach(() => {
        TestBed.configureTestingModule({
            providers: [
                Modal
            ]
        });

        modal = TestBed.get(Modal);
    });

    it("should render a clickable confirm button above the overlay hit target", fakeAsync(() => {
        let dialogRef: DialogRef<void> = null;

        modal.confirm()
            .title("Rotate API Key")
            .body("Confirm rotation")
            .okBtn("Rotate")
            .okBtnClass("btn btn-warning")
            .cancelBtn("Cancel")
            .open()
            .then(ref => dialogRef = ref);

        flushMicrotasks();

        const overlay = document.body.querySelector(".modal-overlay") as HTMLElement;
        const okButton = Array.prototype.slice.call(
            overlay.querySelectorAll("button")
        ).find((button: HTMLButtonElement) => button.textContent.trim() === "Rotate") as HTMLButtonElement;

        expect(dialogRef).toBeDefined();
        expect(overlay).not.toBeNull();
        expect(okButton).not.toBeNull();

        const buttonRect = okButton.getBoundingClientRect();
        const hitTarget = document.elementFromPoint(
            buttonRect.left + buttonRect.width / 2,
            buttonRect.top + buttonRect.height / 2
        ) as HTMLElement;

        expect(hitTarget).toBe(okButton);

        okButton.click();
        flushMicrotasks();

        expect(document.body.querySelector(".modal-overlay")).toBeNull();
    }));

    it("should render formatted body markup while escaping dynamic names", fakeAsync(() => {
        modal.confirm()
            .title("Delete Files")
            .body(Localization.Modal.DELETE_LOCAL_BULK_MESSAGE([
                "Alpha <unsafe>",
                "Beta",
                "Gamma",
                "Delta",
                "Epsilon",
                "Zeta"
            ]))
            .okBtn("Delete")
            .cancelBtn("Cancel")
            .open();

        flushMicrotasks();

        const body = document.body.querySelector(".modal-body") as HTMLElement;

        expect(body).not.toBeNull();
        expect(body.querySelectorAll("ul").length).toBe(1);
        expect(body.querySelectorAll("li").length).toBe(5);
        expect(body.querySelectorAll("br").length).toBe(1);
        expect(body.innerHTML).toContain("<b>Alpha &lt;unsafe&gt;</b>");
        expect(body.innerHTML).toContain("And 1 more file(s).");
    }));
});
