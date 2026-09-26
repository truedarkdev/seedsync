import { Pipe, PipeTransform } from "@angular/core";

/*
 * Convert bytes into largest possible unit.
 * Takes an precision argument that defaults to 2.
 * Usage:
 *   bytes | fileSize:precision
 * Example:
 *   {{ 1024 |  fileSize}}
 *   formats to: 1 KB
 * Source: https://gist.github.com/JonCatmull/ecdf9441aaa37
 *         336d9ae2c7f9cb7289a#file-file-size-pipe-ts
*/
@Pipe({name: "fileSize", standalone: true})
export class FileSizePipe implements PipeTransform {

  private units = [
    "B",
    "KB",
    "MB",
    "GB",
    "TB",
    "PB"
  ];

  transform(bytes: number = 0, precision: number = 2, belowBytes?: number): string {
    const formatted = this.format(bytes, precision);
    if (
      belowBytes == null
      || !Number.isFinite(bytes)
      || !Number.isFinite(belowBytes)
      || bytes < 0
      || bytes >= belowBytes
      || formatted !== this.format(belowBytes, precision)
    ) {
      return formatted;
    }

    const unitSeparator = formatted.lastIndexOf(" ");
    const displayedValue = Number(formatted.slice(0, unitSeparator));
    if (unitSeparator < 0 || !Number.isFinite(displayedValue) || displayedValue <= 0) {
      return formatted;
    }

    const exponent = Math.floor(Math.log10(displayedValue));
    const isPowerOfTen = displayedValue === 10 ** exponent;
    const quantum = 10 ** (exponent - Number(precision) + (isPowerOfTen ? 0 : 1));
    const previousValue = Number((displayedValue - quantum).toPrecision(Number(precision)));
    return Math.max(0, previousValue) + formatted.slice(unitSeparator);
  }

  private format(bytes: number, precision: number): string {
    if ( isNaN( parseFloat( String(bytes) )) || ! isFinite( bytes ) ) { return "?"; }

    let unit = 0;

    while ( bytes >= 1024 ) {
      bytes /= 1024;
      unit ++;
    }

    return Number(bytes.toPrecision( + precision )) + " " + this.units[ unit ];
  }
}
