import argparse

from storage import create_activation_code, get_codes, init_storage, revoke_activation_code, save_server


def main() -> None:
    parser = argparse.ArgumentParser(description='Skryon orchestrator admin CLI')
    sub = parser.add_subparsers(dest='command', required=True)

    new_code = sub.add_parser('new-code')
    new_code.add_argument('--days', type=int, default=30)
    new_code.add_argument('--note', default='')

    sub.add_parser('codes')

    revoke = sub.add_parser('revoke')
    revoke.add_argument('code')

    server = sub.add_parser('set-server')
    server.add_argument('--name', required=True)
    server.add_argument('--region', required=True)
    server.add_argument('--config', required=True)

    args = parser.parse_args()
    init_storage()

    if args.command == 'new-code':
        print(create_activation_code(args.days, args.note))
    elif args.command == 'codes':
        for row in get_codes(30):
            print(row)
    elif args.command == 'revoke':
        print('ok' if revoke_activation_code(args.code) else 'not_found')
    elif args.command == 'set-server':
        save_server(args.name, args.region, args.config)
        print('server_saved')


if __name__ == '__main__':
    main()
