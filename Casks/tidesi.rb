cask "tidesi" do
  arch arm: "apple-silicon", intel: "intel"

  version "4.1.2"
  sha256 arm:   "9a552b1cc97a3bd5a8d2f9ec2abfac1c576d9820d49f7ea8ecaf07b10366239e",
         intel: "6c7a50bb37acd4764dec04d33d9fcd81d7e389d130ac1f12af0533f5197efbe6"

  url "https://github.com/Muddyblack/tidesi/releases/download/v#{version}/tidesi-macos-#{version}-#{arch}.dmg"
  name "tideSI"
  desc "Menu bar monitor for AI provider usage limits"
  homepage "https://github.com/Muddyblack/tidesi"

  livecheck do
    url :url
    strategy :github_latest
  end

  depends_on macos: ">= :ventura"

  app "tideSI.app"

  zap trash: [
    "~/.cache/tidesi",
    "~/.config/tidesi",
    "~/.local/share/tidesi",
  ]

  caveats <<~EOS
    tideSI is not notarized yet. If macOS blocks the first launch, choose
    Open Anyway in System Settings > Privacy & Security.
  EOS
end
