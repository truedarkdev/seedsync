import {fakeAsync, flushMicrotasks, TestBed} from "@angular/core/testing";

import {DialogRef, Modal} from "../../../../services/utils/modal.service";


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
});
