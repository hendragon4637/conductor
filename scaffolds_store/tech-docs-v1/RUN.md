# __APP__

## SETUP
true

## RUN
npx --yes markdownlint-cli2 'docs/**/*.md'

## TEST
none (verification = lint + link + front-matter checks)

## VERIFY
bash gates.sh

## VIEW
ls docs/
