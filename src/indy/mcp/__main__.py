from indy.mcp.server import mcp


def run():
    """Entrypoint for the indy-mcp script."""
    mcp.run(transport='stdio')


if __name__ == '__main__':
    run()
