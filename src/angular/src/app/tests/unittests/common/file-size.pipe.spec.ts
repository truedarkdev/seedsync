import {FileSizePipe} from "../../../common/file-size.pipe";

describe("FileSizePipe", () => {
    const pipe = new FileSizePipe();
    const tebibyte = 1024 ** 4;

    it("lowers a colliding transferred display by one significant-digit step", () => {
        expect(pipe.transform(1.535 * tebibyte, 3, 1.536 * tebibyte)).toBe("1.53 TB");
    });

    it("uses the previous precision value below a power-of-ten boundary", () => {
        expect(pipe.transform(tebibyte, 3, 1.0001 * tebibyte)).toBe("0.999 TB");
    });

    it("preserves exact completion, non-colliding units, zero, and unknown values", () => {
        expect(pipe.transform(1.536 * tebibyte, 3, 1.536 * tebibyte)).toBe("1.54 TB");
        expect(pipe.transform(1.534 * tebibyte, 3, 1.536 * tebibyte)).toBe("1.53 TB");
        expect(pipe.transform(0, 3, 1)).toBe("0 B");
        expect(pipe.transform(0, 3, 0)).toBe("0 B");
        expect(pipe.transform(NaN, 3, 1)).toBe("?");
    });

    it("leaves a differing unit-boundary display unchanged", () => {
        const megabyte = 1024 ** 2;
        expect(pipe.transform(megabyte - 1, 3, megabyte)).toBe("1020 KB");
    });
});
