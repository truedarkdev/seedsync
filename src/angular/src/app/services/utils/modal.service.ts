import { Injectable } from "@angular/core";
import { Observable, Subject } from "rxjs";

export interface DialogRef<T = void> {
  result: Promise<T>;
  onDestroy: Observable<void>;
  overlayRef?: {
    location?: {
      nativeElement: HTMLElement;
    };
  };
}

class ConfirmDialogBuilder {
  private _title = "";
  private _body = "";
  private _okLabel = "OK";
  private _okClassName = "";
  private _cancelLabel = "Cancel";
  private _cancelClassName = "";
  private _isBlocking = false;
  private _showClose = true;

  title(value: string): ConfirmDialogBuilder {
    this._title = value;
    return this;
  }

  body(value: string): ConfirmDialogBuilder {
    this._body = value;
    return this;
  }

  okBtn(value: string): ConfirmDialogBuilder {
    this._okLabel = value;
    return this;
  }

  okBtnClass(value: string): ConfirmDialogBuilder {
    this._okClassName = value;
    return this;
  }

  cancelBtn(value: string): ConfirmDialogBuilder {
    this._cancelLabel = value;
    return this;
  }

  cancelBtnClass(value: string): ConfirmDialogBuilder {
    this._cancelClassName = value;
    return this;
  }

  isBlocking(value: boolean): ConfirmDialogBuilder {
    this._isBlocking = value;
    return this;
  }

  showClose(value: boolean): ConfirmDialogBuilder {
    this._showClose = value;
    return this;
  }

  open(): Promise<DialogRef<void>> {
    const onDestroy = new Subject<void>();
    const overlayRoot = this._createOverlay();
    const dialogRoot = this._createDialog();
    const titleElement = this._createTitle();
    const bodyElement = this._createBody();
    const actionsElement = this._createActions();
    const cancelButton = this._createButton(this._cancelLabel, this._cancelClassName, "button");
    const okButton = this._createButton(this._okLabel, this._okClassName, "button");
    const closeButton = this._showClose ? this._createCloseButton() : null;

    if (titleElement != null) {
      dialogRoot.appendChild(titleElement);
    }
    if (closeButton != null) {
      dialogRoot.appendChild(closeButton);
    }
    if (bodyElement != null) {
      dialogRoot.appendChild(bodyElement);
    }

    actionsElement.appendChild(cancelButton);
    actionsElement.appendChild(okButton);
    dialogRoot.appendChild(actionsElement);
    overlayRoot.appendChild(dialogRoot);
    document.body.appendChild(overlayRoot);

    const dialogRef: DialogRef<void> = {
      result: null,
      onDestroy: onDestroy.asObservable(),
      overlayRef: {
        location: {
          nativeElement: overlayRoot
        }
      }
    };

    const result = new Promise<void>((resolve, reject) => {
      const close = (confirmed: boolean): void => {
        overlayRoot.removeEventListener("click", overlayClickHandler);
        overlayRoot.removeEventListener("keydown", keydownHandler);
        cancelButton.removeEventListener("click", cancelHandler);
        okButton.removeEventListener("click", confirmHandler);
        if (closeButton != null) {
          closeButton.removeEventListener("click", cancelHandler);
        }

        if (overlayRoot.parentNode != null) {
          overlayRoot.parentNode.removeChild(overlayRoot);
        }

        if (confirmed) {
          resolve();
          return;
        }

        reject();
      };

      const cancelHandler = (): void => close(false);
      const confirmHandler = (): void => close(true);
      const overlayClickHandler = (event: MouseEvent): void => {
        if (this._isBlocking || event.target !== overlayRoot) {
          return;
        }

        close(false);
      };
      const keydownHandler = (event: KeyboardEvent): void => {
        if (event.key === "Escape" && !this._isBlocking) {
          event.preventDefault();
          close(false);
        }
      };

      overlayRoot.addEventListener("click", overlayClickHandler);
      overlayRoot.addEventListener("keydown", keydownHandler);
      cancelButton.addEventListener("click", cancelHandler);
      okButton.addEventListener("click", confirmHandler);
      if (closeButton != null) {
        closeButton.addEventListener("click", cancelHandler);
      }
    }).finally(() => {
      onDestroy.next();
      onDestroy.complete();
    });

    dialogRef.result = result;

    return Promise.resolve(dialogRef);
  }

  private _createOverlay(): HTMLElement {
    const overlayRoot = document.createElement("div");
    overlayRoot.className = "modal-overlay";
    overlayRoot.tabIndex = -1;
    overlayRoot.setAttribute("role", "presentation");
    Object.assign(overlayRoot.style, {
      position: "fixed",
      inset: "0",
      zIndex: "1050",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      backgroundColor: "rgba(0, 0, 0, 0.5)"
    });
    return overlayRoot;
  }

  private _createDialog(): HTMLElement {
    const dialogRoot = document.createElement("div");
    dialogRoot.className = "modal-dialog";
    dialogRoot.setAttribute("role", "dialog");
    dialogRoot.setAttribute("aria-modal", "true");
    Object.assign(dialogRoot.style, {
      position: "relative",
      zIndex: "1",
      pointerEvents: "auto",
      width: "100%",
      maxWidth: "32rem",
      margin: "1rem",
      padding: "1.25rem",
      borderRadius: "0.25rem",
      backgroundColor: "#fff",
      boxShadow: "0 1rem 3rem rgba(0, 0, 0, 0.3)"
    });
    return dialogRoot;
  }

  private _createTitle(): HTMLElement | null {
    if (!this._title) {
      return null;
    }

    const titleElement = document.createElement("h4");
    titleElement.textContent = this._title;
    titleElement.style.margin = "0 2rem 0.75rem 0";
    return titleElement;
  }

  private _createBody(): HTMLElement | null {
    if (!this._body) {
      return null;
    }

    const bodyElement = document.createElement("p");
    bodyElement.textContent = this._body;
    bodyElement.style.margin = "0 0 1rem 0";
    bodyElement.style.whiteSpace = "pre-wrap";
    return bodyElement;
  }

  private _createActions(): HTMLElement {
    const actionsElement = document.createElement("div");
    Object.assign(actionsElement.style, {
      display: "flex",
      justifyContent: "flex-end",
      gap: "0.5rem"
    });
    return actionsElement;
  }

  private _createButton(
    label: string,
    className: string,
    type: "button" | "submit" | "reset"
  ): HTMLButtonElement {
    const button = document.createElement("button");
    button.type = type;
    button.textContent = label;
    button.className = className || "";
    return button;
  }

  private _createCloseButton(): HTMLButtonElement {
    const button = this._createButton("x", "close", "button");
    button.setAttribute("aria-label", "Close");
    Object.assign(button.style, {
      position: "absolute",
      top: "0.5rem",
      right: "0.75rem"
    });
    return button;
  }
}

@Injectable({
  providedIn: "root"
})
export class Modal {
  confirm(): ConfirmDialogBuilder {
    return new ConfirmDialogBuilder();
  }
}
