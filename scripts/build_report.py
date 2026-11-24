"""Build a Web Fetch report; the profile (or one --variant) selects the registered variant."""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from harness.report_languages import add_language_argument
from harness.web_fetch.report_variants import VARIANTS, BuildRequest, VariantUsageError, build

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='variants:\n' + '\n'.join(f'  {name}: {item.summary}' for name, item in VARIANTS.items()))
    parser.add_argument('--variant', choices=list(VARIANTS),
                        help='required when no profile selects the variant')
    parser.add_argument('--profile', type=Path, help='external report instance profile')
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--input', type=Path,
                        help='variant input: saved-matrix spec, research JSON or refresh binding')
    parser.add_argument('--output', type=Path)
    add_language_argument(parser)
    parser.add_argument('--include-english', action='store_true',
                        help='generate both editions; --language selects the homepage')
    parser.add_argument('--authority-root', type=Path,
                        help='external authority bundle for historical full-cohort reports')
    parser.add_argument('--wcxb-source', type=Path)
    parser.add_argument('--wcxb-sample', type=Path)
    parser.add_argument('--presentation-binding', type=Path,
                        help='rebuild a hash-bound historical local audit without rescoring')
    args = parser.parse_args()
    profile = args.profile and (args.profile if args.profile.is_absolute() else ROOT / args.profile).resolve()
    request = BuildRequest(
        profile=profile, evidence=args.evidence, input=args.input, output=args.output,
        language=args.language, include_english=args.include_english,
        authority_root=args.authority_root, wcxb_source=args.wcxb_source,
        wcxb_sample=args.wcxb_sample, presentation_binding=args.presentation_binding)
    try:
        print(build(request, args.variant))
    except VariantUsageError as error:
        parser.error(str(error))
