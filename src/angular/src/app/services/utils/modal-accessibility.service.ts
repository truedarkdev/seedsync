import {Injectable} from "@angular/core";

import {DialogRef} from "./modal-compat.service";

@Injectable()
export class ModalAccessibilityService {
    private static readonly FOCUSABLE_SELECTOR = [
        "button:not([disabled])",
        "[href]",
        "input:not([disabled])",
        "select:not([disabled])",
        "textarea:not([disabled])",
        "[tabindex]:not([tabindex='-1'])"
    ].join(", ");

    enhance<T>(dialogRefPromise: Promise<DialogRef<T>>): Promise<DialogRef<T>> {
        const previouslyFocusedElement = document.activeElement as HTMLElement;

        dialogRefPromise.then(dialogRef => {
            const overlayRoot = dialogRef &&
                dialogRef.overlayRef &&
                dialogRef.overlayRef.location &&
                dialogRef.overlayRef.location.nativeElement as HTMLElement;
            if (overlayRoot == null) {
                return;
            }

            const getFocusableElements = (): HTMLElement[] => {
                const elements = Array.prototype.slice.call(
                    overlayRoot.querySelectorAll(ModalAccessibilityService.FOCUSABLE_SELECTOR)
                ) as HTMLElement[];
                return elements.filter(element => {
                    return element.offsetParent !== null && element.getAttribute("aria-hidden") !== "true";
                });
            };

            const focusInitialElement = (): void => {
                const focusableElements = getFocusableElements();
                if (focusableElements.length === 0) {
                    return;
                }

                const cancelButton = focusableElements.find(element => {
                    return element.textContent != null &&
                        element.textContent.trim().toLowerCase() === "cancel";
                });
                (cancelButton || focusableElements[0]).focus();
            };

            const keydownHandler = (event: KeyboardEvent): void => {
                if (event.key !== "Tab") {
                    return;
                }

                const focusableElements = getFocusableElements();
                if (focusableElements.length === 0) {
                    return;
                }

                const firstElement = focusableElements[0];
                const lastElement = focusableElements[focusableElements.length - 1];
                const activeElement = document.activeElement as HTMLElement;

                if (event.shiftKey) {
                    if (activeElement === firstElement || focusableElements.indexOf(activeElement) === -1) {
                        event.preventDefault();
                        lastElement.focus();
                    }
                } else if (activeElement === lastElement || focusableElements.indexOf(activeElement) === -1) {
                    event.preventDefault();
                    firstElement.focus();
                }
            };

            overlayRoot.addEventListener("keydown", keydownHandler);

            dialogRef.onDestroy.subscribe(() => {
                overlayRoot.removeEventListener("keydown", keydownHandler);
                if (previouslyFocusedElement != null && typeof previouslyFocusedElement.focus === "function") {
                    previouslyFocusedElement.focus();
                }
            });

            setTimeout(() => focusInitialElement(), 0);
        });

        return dialogRefPromise;
    }
}
