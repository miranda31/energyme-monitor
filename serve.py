"""Lance le serveur EnergyMe Monitor avec Waitress."""
if __name__ == "__main__":
    from waitress import serve
    from pyramid.paster import get_app
    app = get_app("development.ini#main")
    print("EnergyMe Monitor démarré sur http://0.0.0.0:6543")
    serve(app, host="0.0.0.0", port=6543)
