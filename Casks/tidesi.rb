cask "tidesi" do
  arch arm: "apple-silicon", intel: "intel"

  version "4.1.0"
  sha256 arm:   "ece4c6b048a35fc487463c96e7fc659d2abbc7d806e3bbc14b1f19bd33ef0142",
         intel: "589427aa47cd5529f94428c05466de08554889f5404eb9bd837a6475759e3898"

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
